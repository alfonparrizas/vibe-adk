import functions_framework
import flask # o from flask import jsonify, make_response, request
from google.cloud import bigquery
import datetime # Solo para formatear el timestamp en la respuesta
from typing import Dict, Any, List # Para tipado
import os

# --- Configuración de BigQuery ---
BIGQUERY_PROJECT_ID = os.environ.get("BIGQUERY_PROJECT_ID", "fon-test-project")
BIGQUERY_DATASET_ID = os.environ.get("BIGQUERY_DATASET_ID", "foncorp_travel_data")
BIGQUERY_TABLE_ID = os.environ.get("BIGQUERY_TABLE_ID", "travel_requests") # Tabla con nuevo esquema

# --- Lógica de Negocio Interna (tu función original get_travel_requests_by_status) ---
def _get_travel_requests_from_bq(search_term: str) -> Dict[str, Any]:
    """Consulta solicitudes de viaje y devuelve un diccionario con 'query_result_string'.
    """
    try:
        client = bigquery.Client(project=BIGQUERY_PROJECT_ID)
        table_ref_str = f"{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_ID}.{BIGQUERY_TABLE_ID}"
        status_conditions = []
        query_params = []
        param_counter = 0
        processed_search_term = search_term.lower().strip()

        # Lógica de interpretación del search_term (como la teníamos)
        if "pendiente" in processed_search_term or \
           "sin aprobar" in processed_search_term or \
           "nuevas" in processed_search_term or \
           ("registrada" in processed_search_term and "aprobaci" not in processed_search_term) :
            
            param_counter += 1
            status_conditions.append(f"LOWER(status) = LOWER(@status_param_{param_counter})")
            query_params.append(bigquery.ScalarQueryParameter(f"status_param_{param_counter}", "STRING", "Registrada"))
            
            if "aprobaci" in processed_search_term or "pendiente" in processed_search_term :
                 param_counter += 1
                 if not any(p.value.lower() == "pendiente de aprobación" for p in query_params):
                    status_conditions.append(f"LOWER(status) = LOWER(@status_param_{param_counter})")
                    query_params.append(bigquery.ScalarQueryParameter(f"status_param_{param_counter}", "STRING", "Pendiente de Aprobación"))
        
        exact_final_statuses = ["aprobada", "rechazada", "reservada", "completada", "cancelada"]
        if processed_search_term in exact_final_statuses or \
           (not status_conditions and processed_search_term): # Si no se activó la lógica anterior Y el término no está vacío
            status_conditions = [] 
            query_params = []
            param_counter = 0
            
            param_counter += 1
            status_conditions.append(f"LOWER(status) = LOWER(@status_param_{param_counter})")
            # Pasamos el search_term original para que BigQuery (con LOWER()) maneje la capitalización
            query_params.append(bigquery.ScalarQueryParameter(f"status_param_{param_counter}", "STRING", search_term.strip()))

        if not status_conditions:
             print(f"Término de búsqueda no interpretado en _get_travel_requests_from_bq: '{search_term}'.")
             return {"query_result_string": f"No pude interpretar el término de búsqueda de estado: '{search_term}'. Por favor, usa estados conocidos."}

        where_clause = " OR ".join(status_conditions)
        # Asegúrate de que los nombres de columna coincidan con tu tabla BQ (employee_first_name, etc.)
        query = f"""
            SELECT request_id, timestamp, employee_first_name, employee_last_name, employee_id, 
                   origin_city, destination_city, start_date, end_date, transport_mode, car_type, reason, status
            FROM `{table_ref_str}` WHERE {where_clause} ORDER BY timestamp DESC LIMIT 10
        """
        job_config = bigquery.QueryJobConfig(query_parameters=query_params)
        query_job = client.query(query, job_config=job_config)
        results = query_job.result()

        if results.total_rows == 0:
            print(f"No se encontraron solicitudes para '{search_term}' en _get_travel_requests_from_bq.")
            return {"query_result_string": f"No se encontraron solicitudes de viaje para el término de búsqueda: '{search_term}'."}

        found_requests_str_list = []
        for row in results:
            # Formatear cada solicitud como un string legible
            timestamp_str = row.timestamp.strftime('%Y-%m-%d %H:%M') if row.timestamp else 'N/A'
            car_info = f' ({row.car_type})' if row.car_type and row.transport_mode and row.transport_mode.lower() == 'coche' else ''
            request_summary = (
                f"ID: {row.request_id}, Empleado: {row.employee_first_name} {row.employee_last_name} (ID: {row.employee_id}), "
                f"Ruta: {row.origin_city} a {row.destination_city}, Fechas: {row.start_date} a {row.end_date}, "
                f"Transporte: {row.transport_mode}{car_info}, "
                f"Motivo: {row.reason}, Estado: {row.status}, Registrada: {timestamp_str}"
            )
            found_requests_str_list.append(request_summary)
        
        final_response_str = f"Se encontraron {len(found_requests_str_list)} solicitudes para '{search_term}':\n" + "\n".join(found_requests_str_list)
        print(f"Respuesta de _get_travel_requests_from_bq: {final_response_str}")
        return {"query_result_string": final_response_str}

    except Exception as e:
        print(f"ERROR GENERAL en _get_travel_requests_from_bq: {e}")
        return {"query_result_string": f"Error técnico al consultar las solicitudes de viaje: {str(e)}."}


# Punto de entrada para la Cloud Function HTTP de 2ª Generación
@functions_framework.http
def consultar_viajes_tool_webhook(request: flask.Request) -> flask.Response:
    """Cloud Function HTTP para consultar solicitudes de viaje por estado."""
    if request.method != 'POST':
        return flask.make_response(("Método no permitido", 405))

    try:
        request_json = request.get_json(silent=True)
        if not request_json:
            return flask.make_response(flask.jsonify({"error": "Solicitud JSON inválida o vacía"}), 400)

        print(f"Request JSON recibido en consultar_viajes_tool_webhook: {request_json}")

        search_term = request_json.get("search_term") # Coincide con la OpenAPI spec
        if search_term is None: # `search_term` podría ser una cadena vacía, lo que es válido
            return flask.make_response(flask.jsonify({"tool_response_message": "Falta el parámetro requerido 'search_term'."}), 400)
        
        # Llamar a la lógica de negocio
        result_dict = _get_travel_requests_from_bq(search_term=search_term)

        # La respuesta de la tool para Playbooks debe ser un JSON con los parámetros de salida definidos en OpenAPI
        playbook_tool_response = {
            "query_results_string": result_dict.get("query_result_string")
        }
        
        print(f"Respuesta del webhook consultar_viajes_tool_webhook: {playbook_tool_response}")
        return flask.jsonify(playbook_tool_response)

    except Exception as e:
        print(f"Error general en el webhook consultar_viajes_tool_webhook: {e}")
        error_response_payload = {
            "query_results_string": f"Error interno crítico en la herramienta de consulta: {str(e)}"
        }
        return flask.make_response(flask.jsonify(error_response_payload), 500)

# Podrías añadir aquí también el @functions_framework.http para registrar_viaje_tool_webhook
# si quieres tener ambas en el mismo main.py, o mantenerlas en archivos separados.
# Por ahora, me centro en la nueva.