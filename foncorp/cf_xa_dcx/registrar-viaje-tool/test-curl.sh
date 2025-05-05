curl -X POST -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
     -H "Content-Type: application/json" \
     -d '{
           "employee_first_name": "Ana",
           "employee_last_name": "Pérez",
           "employee_id": "FP4567",
           "origin_city": "Sevilla",
           "destination_city": "Valencia",
           "start_date": "2025-06-10",
           "end_date": "2025-06-12",
           "transport_mode": "Avión",
           "reason": "Conferencia Test",
           "car_type": null
         }' \
     https://europe-west1-fon-test-project.cloudfunctions.net/registrar-viaje-tool