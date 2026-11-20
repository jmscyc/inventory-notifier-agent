#!/usr/bin/env bash
set -e

PROJECT_ID=$(gcloud config get-value project)
SERVICE_NAME="inventory-notifier-web"
REGION="us-central1"

echo "======================================================="
echo "  開始部署【庫存監控與自動化工作台】至 Google Cloud Run"
echo "  GCP 專案 ID: ${PROJECT_ID}"
echo "  部署區域: ${REGION}"
echo "  服務名稱: ${SERVICE_NAME}"
echo "======================================================="

echo "[1/2] 檢查並啟用 GCP 必要 API..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com --project="${PROJECT_ID}"

echo "[2/2] 正在建置並部署容器映像檔至 Cloud Run (免費額度配置: 0~1 縮容, 512MB)..."
gcloud run deploy "${SERVICE_NAME}" \
  --source . \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 1 \
  --memory 512Mi \
  --cpu 1 \
  --timeout 300s \
  --set-env-vars "TZ=Asia/Taipei"

SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --platform managed --region "${REGION}" --project "${PROJECT_ID}" --format 'value(status.url)')

echo "======================================================="
echo "🎉 Cloud Run 部署成功！"
echo "🌐 雲端正式 HTTPS 網址: ${SERVICE_URL}"
echo "======================================================="
