import rpyc
import sys
import time

MAX_RETRIES = 3  # Number of retry attempts for leader detection

def submit_command(node_id, command, attempt=1):
    """Attempts to submit a command to a Raft node, redirecting to leader if necessary."""
    try:
        hostname = "localhost"
        port = 18861 + node_id
        print(f"🔗 Attempting to connect to Node {node_id} at {hostname}:{port} (Attempt {attempt})...")
        conn = rpyc.connect(hostname, port)

        success, leader_id = conn.root.submit_command(command)

        if success:
            print(f"✅ Command '{command}' successfully submitted to Leader Node {node_id}")
        else:
            if leader_id is None:
                print(f"⚠️ No leader available. Retrying in 2 seconds...")
                time.sleep(2)
                if attempt < MAX_RETRIES:
                    submit_command(node_id, command, attempt + 1)
                else:
                    print("❌ Leader election is taking too long. Please try again later.")
                return
            
            print(f"🔄 Node {node_id} is not the leader, redirecting to Leader Node {leader_id}...")
            submit_command(leader_id, command)

        conn.close()
    except (ConnectionRefusedError, EOFError) as e:
        print(f"⚠️ Node {node_id} is unreachable. Retrying in 2 seconds...")
        time.sleep(2)
        if attempt < MAX_RETRIES:
            submit_command(node_id, command, attempt + 1)
        else:
            print("❌ Unable to connect to any node. Ensure the cluster is running.")
    except KeyboardInterrupt:
        print("\n❌ Operation canceled by user.")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python client.py <node_id> <command>")
        sys.exit(1)

    node_id = int(sys.argv[1])
    command = sys.argv[2]
    
    submit_command(node_id, command)
