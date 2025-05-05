import functions_framework
import flask # o from flask import jsonify, make_response, request
from google.cloud import bigquery
import datetime # Para el timestamp de actualización
from typing import Dict, Any # Para tipado
import os

# --- Configuración de BigQuery (asumimos que ya está definida como en las otras funciones) ---
BIGQUERY_PROJECT_ID = os.environ.get("BIGQUERY_PROJECT_ID", "fon-test-project")
BIGQUERY_DATASET_ID = os.environ.get("BIGQUERY_DATASET_ID", "foncorp_travel_data")
BIGQUERY_TABLE_ID = os.environ.get("BIGQUERY_TABLE_ID", "travel_requests")

# --- Lógica de Negocio Interna (tu función original update_travel_request_status) ---
def _update_travel_status_in_bq(request_id: str, new_status: str) -> Dict[str, Any]:
    """Actualiza el estado de una solicitud de viaje en BigQuery.
    Devuelve un diccionario con 'status_message'.
    """
    valid_statuses = ["Registrada", "Pendiente de Aprobación", "Aprobada", "Rechazada", "Reservada", "Completada", "Cancelada"]
    
    # Normalizar el new_status para comparación y para guardarlo consistentemente
    normalized_new_status_input = new_status.strip().lower()
    final_status_to_save = new_status.strip().capitalize() # Capitalizar el input del usuario por defecto

    # Mapeo simple para términos comunes que podría pasar el LLM
    if "pendiente de aprobaci" in normalized_new_status_input:
        final_status_to_save = "Pendiente de Aprobación"
    elif "registrada" in normalized_new_status_input:
        final_status_to_save = "Registrada"
    # Añadir más mapeos si es necesario para otros estados normalizados
    elif "aprobada" in normalized_new_status_input:
        final_status_to_save = "Aprobada"
    elif "rechazada" in normalized_new_status_input:
        final_status_to_save = "Rechazada"
    elif "reservada" in normalized_new_status_input:
        final_status_to_save = "Reservada"
    elif "completada" in normalized_new_status_input:
        final_status_to_save = "Completada"
    elif "cancelada" in normalized_new_status_input:
        final_status_to_save = "Cancelada"
    
    if final_status_to_save not in valid_statuses:
        return {"status_message": f"Error: '{new_status}' (interpretado como '{final_status_to_save}') no es un estado válido. Los estados válidos son: {', '.join(valid_statuses)}."}

    try:
        client = bigquery.Client(project=BIGQUERY_PROJECT_ID)
        table_ref_str = f"{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_ID}.{BIGQUERY_TABLE_ID}"
        
        query = f"""
            UPDATE `{table_ref_str}`
            SET status = @new_status_param, timestamp = @current_timestamp_param
            WHERE request_id = @request_id_param
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("new_status_param", "STRING", final_status_to_save),
                bigquery.ScalarQueryParameter("request_id_param", "STRING", request_id),
                bigquery.ScalarQueryParameter("current_timestamp_param", "TIMESTAMP", datetime.datetime.now(datetime.timezone.utc).isoformat())
            ]
        )
        query_job = client.query(query, job_config=job_config)
        query_job.result()  # Esperar a que el UPDATE termine

        if query_job.num_dml_affected_rows is not None and query_job.num_dml_affected_rows > 0:
            success_message = f"El estado de la solicitud de viaje con ID '{request_id}' ha sido actualizado exitosamente a '{final_status_to_save}'."
            return {"status_message": success_message}
        else:
            # Esto puede ocurrir si el request_id no existe o el estado ya era el new_status
            not_found_message = f"No se encontró una solicitud de viaje con ID '{request_id}' o el estado ya era '{final_status_to_save}' (no se realizaron cambios)."
            return {"status_message": not_found_message}

    except Exception as e:
        print(f"ERROR GENERAL en _update_travel_status_in_bq: {e}")
        return {"status_message": f"Error técnico al actualizar el estado de la solicitud '{request_id}': {str(e)}."}


# Punto de entrada para la Cloud Function HTTP de 2ª Generación
@functions_framework.http
def actualizar_viaje_tool_webhook(request: flask.Request) -> flask.Response:
    """Cloud Function HTTP para actualizar el estado de una solicitud de viaje."""
    if request.method != 'POST':
        return flask.make_response(("Método no permitido", 405))

    try:
        request_json = request.get_json(silent=True)
        if not request_json:
            return flask.make_response(flask.jsonify({"error": "Solicitud JSON inválida o vacía"}), 400)

        print(f"Request JSON recibido en actualizar_viaje_tool_webhook: {request_json}")

        request_id = request_json.get("request_id")
        new_status = request_json.get("new_status")

        if not request_id or not new_status:
            missing = []
            if not request_id: missing.append("'request_id'")
            if not new_status: missing.append("'new_status'")
            return flask.make_response(flask.jsonify({"tool_response_message": f"Faltan parámetros requeridos: {', '.join(missing)}."}), 400)
        
        # Llamar a la lógica de negocio
        result_dict = _update_travel_status_in_bq(request_id=request_id, new_status=new_status)

        # La respuesta de la tool para Playbooks
        playbook_tool_response = {
            "update_status_message": result_dict.get("status_message") # El nombre de este parámetro de salida lo definiremos en OpenAPI
        }
        
        print(f"Respuesta del webhook actualizar_viaje_tool_webhook: {playbook_tool_response}")
        return flask.jsonify(playbook_tool_response)

    except Exception as e:
        print(f"Error general en el webhook actualizar_viaje_tool_webhook: {e}")
        error_response_payload = {
            "update_status_message": f"Error interno crítico en la herramienta de actualización: {str(e)}"
        }
        return flask.make_response(flask.jsonify(error_response_payload), 500)

# Aquí podrías tener también las otras funciones HTTP si están en el mismo main.py:
# @functions_framework.http
# def registrar_viaje_tool_webhook(request: flask.Request) -> flask.Response: ...
# @functions_framework.http
# def consultar_viajes_tool_webhook(request: flask.Request) -> flask.Response: ...