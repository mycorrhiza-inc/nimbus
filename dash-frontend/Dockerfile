FROM python:3.12.4-alpine3.20

WORKDIR /app

COPY requirements.txt /app/requirements.txt
# Uncomment for debugging python dependancy issue
# COPY requirements-unlocked.txt /app/requirements.txt

RUN pip install --no-cache -r requirements.txt 

COPY ./app.py /app/app.py

CMD ["python3","app.py"]
