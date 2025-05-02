import functions_framework
import flask # o from flask import jsonify, make_response, request
from google.cloud import bigquery
import uuid
import datetime
from typing import Optional, Dict, Any
import os

# --- Configuración de BigQuery ---
# Leer de variables de entorno (se configuran al desplegar la Cloud Function)
BIGQUERY_PROJECT_ID = os.environ.get("BIGQUERY_PROJECT_ID", "fon-test-project") # Tu proyecto
BIGQUERY_DATASET_ID = os.environ.get("BIGQUERY_DATASET_ID", "foncorp_travel_data")
BIGQUERY_TABLE_ID = os.environ.get("BIGQUERY_TABLE_ID", "travel_requests") # Tabla con nuevo esquema

# --- Lógica de Negocio Interna (similar a la que ya teníamos en ADK) ---
def _register_travel_in_bq(
    employee_first_name: str,
    employee_last_name: str,
    employee_id: str,
    origin_city: str,
    destination_city: str,
    start_date: str,
    end_date: str,
    transport_mode: str,
    reason: str,
    car_type: Optional[str] = None
) -> Dict[str, Any]:
    """Registra una solicitud de viaje en BigQuery usando DML INSERT.
    Devuelve un diccionario con 'status_message' y opcionalmente 'request_id'.
    """
    try:
        # Validación de fechas
        date_format = "%Y-%m-%d"
        current_date_obj = datetime.datetime.now().date()
        start_date_obj = datetime.datetime.strptime(start_date, date_format).date()
        end_date_obj = datetime.datetime.strptime(end_date, date_format).date()

        if start_date_obj < current_date_obj:
            return {"status_message": f"Error en la herramienta: La fecha de inicio '{start_date}' ya ha pasado."}
        if end_date_obj < current_date_obj:
             return {"status_message": f"Error en la herramienta: La fecha de fin '{end_date}' ya ha pasado."}
        if end_date_obj < start_date_obj:
            return {"status_message": "Error en la herramienta: La fecha de fin no puede ser anterior a la fecha de inicio."}
    except ValueError:
        return {"status_message": "Error en la herramienta: El formato de las fechas no es válido. Utiliza YYYY-MM-DD."}
    except Exception as e: # Captura otras excepciones de parseo de fechas
        print(f"Error de validación de fechas: {e}")
        return {"status_message": f"Error de validación de fechas: {str(e)}."}


    try:
        # Usar el project_id configurado para el cliente de BQ
        client = bigquery.Client(project=BIGQUERY_PROJECT_ID)
        request_id_val = str(uuid.uuid4())
        current_timestamp = datetime.datetime.now(datetime.timezone.utc)
        initial_status = "Registrada" # Esquema v2
        table_ref_str = f"{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_ID}.{BIGQUERY_TABLE_ID}"

        query = f"""
            INSERT INTO `{table_ref_str}` (
                request_id, timestamp, employee_first_name, employee_last_name, employee_id,
                origin_city, destination_city, start_date, end_date,
                transport_mode, car_type, reason, status
            ) VALUES (
                @request_id, @timestamp, @employee_first_name, @employee_last_name, @employee_id,
                @origin_city, @destination_city, @start_date, @end_date,
                @transport_mode, @car_type, @reason, @status
            )
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("request_id", "STRING", request_id_val),
                bigquery.ScalarQueryParameter("timestamp", "TIMESTAMP", current_timestamp.isoformat()),
                bigquery.ScalarQueryParameter("employee_first_name", "STRING", employee_first_name),
                bigquery.ScalarQueryParameter("employee_last_name", "STRING", employee_last_name),
                bigquery.ScalarQueryParameter("employee_id", "STRING", employee_id),
                bigquery.ScalarQueryParameter("origin_city", "STRING", origin_city),
                bigquery.ScalarQueryParameter("destination_city", "STRING", destination_city),
                bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
                bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
                bigquery.ScalarQueryParameter("transport_mode", "STRING", transport_mode),
                bigquery.ScalarQueryParameter("car_type", "STRING", car_type), # BQ maneja None como NULL
                bigquery.ScalarQueryParameter("reason", "STRING", reason),
                bigquery.ScalarQueryParameter("status", "STRING", initial_status),
            ]
        )
        query_job = client.query(query, job_config=job_config)
        query_job.result() 

        if query_job.errors:
            error_messages = "; ".join([str(error["message"]) for error in query_job.errors])
            print(f"ERROR BQ DML en _register_travel_in_bq: {error_messages}")
            return {"status_message": f"Error al registrar la solicitud en BigQuery: {error_messages}."}
        else:
            if query_job.num_dml_affected_rows is not None and query_job.num_dml_affected_rows > 0:
                full_name = f"{employee_first_name} {employee_last_name}"
                confirmation_message = (
                    f"¡Solicitud registrada con éxito! ID: {request_id_val}. "
                    f"Para {full_name} (ID: {employee_id}) desde {origin_city} a {destination_city} "
                    f"({start_date} a {end_date}), usando {transport_mode}"
                    f"{f' ({car_type})' if car_type and transport_mode.lower() == 'coche' else ''}. Motivo: {reason}."
                )
                return {"status_message": confirmation_message, "request_id": request_id_val}
            else:
                print(f"ERROR BQ DML en _register_travel_in_bq: No se afectaron filas.")
                return {"status_message": "Error al registrar la solicitud: no se insertaron filas."}
    except Exception as e:
        print(f"ERROR GENERAL en _register_travel_in_bq: {e}")
        return {"status_message": f"Error técnico al registrar la solicitud: {str(e)}."}

# Punto de entrada para la Cloud Function HTTP de 2ª Generación
@functions_framework.http
def registrar_viaje_tool_webhook(request: flask.Request) -> flask.Response:
    """Cloud Function HTTP para registrar una solicitud de viaje.
    Espera un JSON con los parámetros definidos en la OpenAPI spec de la tool.
    """
    # El request de Dialogflow CX para una tool de Playbook viene con los parámetros
    # dentro de un campo `tool_input` si la OpenAPI spec lo define así, o directamente.
    # La OpenAPI que definimos antes espera los parámetros directamente en el cuerpo.
    # Si no, Dialogflow CX los envía bajo una clave genérica como "parameters" o el nombre
    # del esquema del requestBody. La documentación de Playbook Tools es clave aquí.
    # Asumamos que la OpenAPI se define para que los parámetros estén en la raíz del JSON del request.

    if request.method != 'POST':
        return flask.make_response(("Método no permitido", 405))

    try:
        request_json = request.get_json(silent=True)
        if not request_json:
            return flask.make_response(flask.jsonify({"error": "Solicitud JSON inválida o vacía"}), 400)

        print(f"Request JSON recibido: {request_json}") # Para depuración

        # Extraer argumentos esperados, manejando los opcionales
        # Los nombres aquí deben coincidir con las propiedades definidas en el requestBody de la OpenAPI spec
        args = {
            "employee_first_name": request_json.get("employee_first_name"),
            "employee_last_name": request_json.get("employee_last_name"),
            "employee_id": request_json.get("employee_id"),
            "origin_city": request_json.get("origin_city"),
            "destination_city": request_json.get("destination_city"),
            "start_date": request_json.get("start_date"),
            "end_date": request_json.get("end_date"),
            "transport_mode": request_json.get("transport_mode"),
            "reason": request_json.get("reason"),
            "car_type": request_json.get("car_type") # Será None si no está presente
        }

        required_fields = ["employee_first_name", "employee_last_name", "employee_id", "origin_city",
                           "destination_city", "start_date", "end_date", "transport_mode", "reason"]
        missing_fields = [field for field in required_fields if args.get(field) is None]
        if missing_fields:
            # Para Playbook tools, la respuesta debe ser un JSON que el playbook pueda interpretar.
            # Devolver un error claro es útil.
            return flask.make_response(flask.jsonify({"tool_response_message": f"Faltan campos requeridos para la herramienta: {', '.join(missing_fields)}"}), 400)

        # Llamar a la lógica de negocio
        result_dict = _register_travel_in_bq(**args)

        # Construir la respuesta JSON que Dialogflow CX Playbook espera para la tool.
        # La documentación de Playbook tools indica que la respuesta debe ser un JSON
        # donde las claves son los nombres de los parámetros de SALIDA definidos en la OpenAPI spec.
        # Si nuestra OpenAPI define 'tool_response_message' y 'generated_request_id' como salidas:
        playbook_tool_response = {
            "tool_response_message": result_dict.get("status_message"),
            "generated_request_id": result_dict.get("request_id") # Puede ser None si hubo error
        }
        # Si solo se espera un string de vuelta, y el playbook lo asigna a un parámetro:
        # playbook_tool_response = {
        # "nombre_del_parametro_de_salida_en_playbook": result_dict.get("status_message")
        # }
        # Por ahora, mantendremos la estructura con 'tool_response_message'.

        print(f"Respuesta del webhook: {playbook_tool_response}")
        return flask.jsonify(playbook_tool_response)

    except Exception as e:
        print(f"Error general en el webhook registrar_viaje_tool_webhook: {e}")
        # Respuesta de error genérica para Dialogflow
        error_response_payload = {
            "tool_response_message": f"Error interno crítico en la herramienta de registro: {str(e)}"
        }
        return flask.make_response(flask.jsonify(error_response_payload), 500)