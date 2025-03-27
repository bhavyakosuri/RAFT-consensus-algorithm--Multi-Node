import random
import time
import threading
import json
import os
import rpyc
from rpyc.utils.server import ThreadedServer
import logging
from enum import Enum

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class State(Enum):
    FOLLOWER = 1
    CANDIDATE = 2
    LEADER = 3

class RaftNode(rpyc.Service):
    def __init__(self, node_id, all_nodes):
        self.node_id = node_id
        self.all_nodes = all_nodes
        self.state = State.FOLLOWER
        self.current_term = 0
        self.voted_for = None
        self.log = []
        self.commit_index = 0
        self.leader_id = None
        self.next_index = {node: 0 for node in all_nodes if node != node_id}
        self.match_index = {node: 0 for node in all_nodes if node != node_id}

        self.storage_file = f"raft_state_{node_id}.json"
        self.load_state()

        self.election_timeout = random.uniform(3.0, 6.0)  # Increased randomness
        self.heartbeat_interval = 1.0

        self.running = True
        self.lock = threading.Lock()
        self.election_timer = None
        self.reset_election_timer()

    def load_state(self):
        """Loads node state from a persistent JSON file."""
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, 'r') as f:
                    state = json.load(f)
                    self.current_term = state.get("current_term", 0)
                    self.voted_for = state.get("voted_for", None)
                    self.log = state.get("log", [])
            except Exception as e:
                logger.error(f"Node {self.node_id}: Failed to load state: {e}")

    def save_state(self):
        """Saves node state to a persistent JSON file."""
        try:
            with open(self.storage_file, 'w') as f:
                json.dump({
                    "current_term": self.current_term,
                    "voted_for": self.voted_for,
                    "log": self.log
                }, f)
        except Exception as e:
            logger.error(f"Node {self.node_id}: Failed to save state: {e}")

    def start_election(self):
        """Initiates a leader election if the node is not already a leader."""
        with self.lock:
            if self.state == State.LEADER:
                return

            self.state = State.CANDIDATE
            self.current_term += 1
            self.voted_for = self.node_id
            self.save_state()
            self.votes = 1  # Candidate votes for itself

            logger.info(f"Node {self.node_id}: Starting election for term {self.current_term}")

        def request_vote(node_id):
            try:
                conn = rpyc.connect("localhost", 18861 + node_id)
                granted = conn.root.request_vote(self.current_term, self.node_id, len(self.log)-1, self.log[-1][0] if self.log else 0)
                conn.close()
                if granted:
                    with self.lock:
                        self.votes += 1
            except:
                pass

        threads = []
        for node_id in self.all_nodes:
            if node_id != self.node_id:
                t = threading.Thread(target=request_vote, args=(node_id,))
                threads.append(t)
                t.start()

        for t in threads:
            t.join()

        with self.lock:
            if self.votes > len(self.all_nodes) // 2:
                self.become_leader()
            else:
                self.state = State.FOLLOWER
                self.reset_election_timer()

    def become_leader(self):
        """Transitions the node to the leader role and starts heartbeats."""
        with self.lock:
            logger.info(f"Node {self.node_id}: Elected as leader for term {self.current_term}")
            self.state = State.LEADER
            self.leader_id = self.node_id
            for node in self.all_nodes:
                if node != self.node_id:
                    self.next_index[node] = len(self.log)
                    self.match_index[node] = 0

        self.send_heartbeats()

    def send_heartbeats(self):
        """Sends heartbeat messages to followers."""
        while self.running and self.state == State.LEADER:
            with self.lock:
                for node_id in self.all_nodes:
                    if node_id != self.node_id:
                        self.replicate_log(node_id)
            time.sleep(self.heartbeat_interval)

    def replicate_log(self, node_id):
        """Replicates log entries to a follower."""
        try:
            conn = rpyc.connect("localhost", 18861 + node_id)
            prev_log_index = self.next_index[node_id] - 1
            prev_log_term = self.log[prev_log_index][0] if prev_log_index >= 0 else 0
            entries = self.log[prev_log_index + 1:]

            success = conn.root.append_entries(self.current_term, self.node_id, prev_log_index, prev_log_term, entries, self.commit_index)
            conn.close()

            if success:
                self.next_index[node_id] = len(self.log)
                self.match_index[node_id] = len(self.log) - 1
        except Exception as e:
            logger.warning(f"Node {self.node_id}: Failed to replicate log to Node {node_id}: {e}")

    def exposed_request_vote(self, term, candidate_id, last_log_index, last_log_term):
        """Handles incoming vote requests."""
        with self.lock:
            if term < self.current_term:
                return False
            if term > self.current_term:
                self.current_term = term
                self.voted_for = None
                self.state = State.FOLLOWER
            if self.voted_for is None or self.voted_for == candidate_id:
                self.voted_for = candidate_id
                self.save_state()
                return True
            return False

    def exposed_append_entries(self, term, leader_id, prev_log_index, prev_log_term, entries, leader_commit):
        """Handles log replication and heartbeats from the leader."""
        with self.lock:
            if term < self.current_term:
                return False

            self.current_term = term
            self.leader_id = leader_id
            self.state = State.FOLLOWER
            self.reset_election_timer()

            if prev_log_index >= len(self.log) or (prev_log_index >= 0 and self.log[prev_log_index][0] != prev_log_term):
                return False

            if entries:
                self.log = self.log[:prev_log_index + 1] + entries
                self.save_state()

            if leader_commit > self.commit_index:
                self.commit_index = min(leader_commit, len(self.log) - 1)

            return True

    def exposed_submit_command(self, command):
        """Handles client command submission."""
        with self.lock:
            if self.state != State.LEADER:
                return False, self.leader_id
            self.log.append((self.current_term, command))
            self.save_state()
            return True, self.node_id

if __name__ == "__main__":
    import sys
    node_id = int(sys.argv[1])
    all_nodes = [0, 1, 2]
    node = RaftNode(node_id, all_nodes)
    server = ThreadedServer(node, port=18861 + node_id, protocol_config={"allow_public_attrs": True})
    logger.info(f"Node {node_id}: Starting server...")
    server.start()
