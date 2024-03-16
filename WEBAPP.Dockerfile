FROM nikolaik/python-nodejs:python3.10-nodejs20-slim

WORKDIR /app

# Copy source code
COPY frontend ./frontend
COPY src ./src
COPY config.py loader.py WEBAPP.py ./

# Copy data
COPY config/ ./config
COPY data/ ./data
COPY imgs/ ./imgs

# Install dependencies
RUN apt-get update -y && \
		apt-get install -y git

COPY WEBAPP.requirements.txt .
RUN pip install -r WEBAPP.requirements.txt

RUN npm install --prefix frontend/

# Build
RUN npm run build --prefix frontend/

# Load data onto the database
RUN python loader.py

EXPOSE 5000
ENTRYPOINT [ "python", "WEBAPP.py" ]
