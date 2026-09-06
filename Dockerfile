# DONN PoC 컨테이너 이미지. Render(Docker), Hugging Face Spaces(Docker), Azure Web App for Containers 공통.
# 실행 포트는 PORT 환경변수(없으면 3666). 키는 이미지에 넣지 않고 호스트의 환경변수로 준다.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/donn

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .
RUN mkdir -p data/cache

EXPOSE 3666
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-3666}"]
