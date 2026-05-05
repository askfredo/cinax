Copy

FROM python:3.11-slim
 
RUN apt-get update && apt-get install -y libgomp1 && rm -rf /var/lib/apt/lists/*
 
WORKDIR /app
 
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
 
COPY . .
 
CMD ["sh", "-c", "python dashboard.py & sleep 3 && echo 'DASHBOARD OK' && python cinax_v02.py"]
 
