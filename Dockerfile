FROM python:3.9-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY raft_node.py .

# Add volume for persistent storage
VOLUME /app/data
CMD ["python", "raft_node.py"]
