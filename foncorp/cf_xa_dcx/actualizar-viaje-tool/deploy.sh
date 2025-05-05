gcloud functions deploy actualizar-viaje-tool \
--gen2 \
--runtime=python312 \
--region=europe-west1 \
--entry-point=actualizar_viaje_tool_webhook \
--trigger-http \
--allow-unauthenticated \
--set-env-vars=BIGQUERY_PROJECT_ID=fon-test-project,BIGQUERY_DATASET_ID=foncorp_travel_data,BIGQUERY_TABLE_ID=travel_requests \
--project=fon-test-project
