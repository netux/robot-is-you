FROM python:3.10-slim

WORKDIR /app

# Copy source code
COPY src ./src
COPY config.py loader.py WEBAPP.py ./

# Copy data
COPY config/ ./config
COPY data/ ./data
COPY imgs/ ./imgs

# Install dependencies
RUN apt-get update -y && \
		apt-get install -y git

COPY requirements.txt .
RUN pip install -r requirements.txt

# Load data onto the database
RUN python loader.py

EXPOSE 5000
ENTRYPOINT [ "python", "WEBAPP.py" ]
