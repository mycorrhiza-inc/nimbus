FROM conda/miniconda3:latest

RUN conda install -c conda-forge switch_model coincbc conda


COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache -r requirements.txt 
# RUN conda install -c conda-forge switch_model coincbc

WORKDIR /app

COPY . . 
