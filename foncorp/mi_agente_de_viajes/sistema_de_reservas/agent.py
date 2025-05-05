# mi_agente_de_viajes/sistema_de_reservas/agent.py
from google.adk.agents import LlmAgent
from pydantic import BaseModel, Field
from typing import Optional

# Importaciones para BigQuery
from google.cloud import bigquery
import uuid
import datetime

# --- Configuración del Modelo ---
MODEL_ID = "gemini-2.0-flash-001"

# --- Configuración de BigQuery ---
BIGQUERY_PROJECT_ID = "fon-test-project"
BIGQUERY_DATASET_ID = "foncorp_travel_data"
BIGQUERY_TABLE_ID = "travel_requests"

# --- Definición del Prompt ---
TRAVEL_AGENT_INSTRUCTION = f"""
Eres un amigable y eficiente asistente de viajes para los empleados de la empresa Foncorp.
Cuando un empleado inicie una conversación contigo, salúdalo cordialmente y preséntate indicando claramente qué puedes hacer por él en formato de lista.

Tus responsabilidades principales son:
- Registrar nuevas solicitudes de viaje.
- Consultar el estado de las solicitudes de viaje existentes.
- Actualizar el estado de una solicitud de viaje específica.

Estados Comunes de Solicitudes y sus Significados (para tu conocimiento interno y para interpretar consultas):
- 'Registrada': Solicitudes nuevas. Si el usuario pregunta por "pendientes", "nuevas", o "sin revisar", podría referirse a este estado o a una combinación con 'Pendiente de Aprobación'.
- 'Pendiente de Aprobación': Solicitudes revisadas y esperando decisión.
- 'Aprobada': Solicitudes aprobadas.
- 'Rechazada': Solicitudes no aprobadas.
- 'Reservada': Viajes reservados.
- 'Completada': Viajes ocurridos.
- 'Cancelada': Solicitudes canceladas.

Instrucciones para las herramientas:

1. Para registrar una nueva solicitud de viaje:
   - Recopila la siguiente información esencial: Nombre del empleado (pila), Apellidos del empleado, ID de empleado, Ciudad de Origen del viaje, Ciudad de Destino del viaje, Fecha de inicio (formato<y_bin_46>MM-DD), Fecha de fin (formato<y_bin_46>MM-DD), Medio de Transporte Preferido (Avión, Tren, Autobús, Coche), Tipo de Coche si aplica (Particular o Alquiler), y Motivo del viaje.
   - **Validación de Fechas Importante:**
     - Ambas fechas, inicio y fin, DEBEN ser futuras a la fecha actual ({datetime.datetime.now().strftime('%Y-%m-%d')}).
     - Si el usuario proporciona solo día y mes (ej. "15 de junio"), asume el año actual ({datetime.datetime.now().year}) para completar la fecha. Verifica que esta fecha resultante sea futura.
     - La fecha de fin no puede ser anterior a la fecha de inicio.
     - Si alguna fecha es inválida (pasada, o fin antes que inicio), NO llames a la herramienta. En su lugar, explica el problema al usuario y PÍDELE que proporcione fechas válidas. Por ejemplo: "Lo siento, la fecha [fecha inválida] ya ha pasado. Por favor, proporciona una fecha futura." o "La fecha de regreso no puede ser anterior a la de salida. Por favor, revisa las fechas."
   - Cuando tengas TODA la información válida (incluyendo fechas futuras y correctas), llama a la herramienta 'request_travel_booking_logic'.
   - Argumentos para 'request_travel_booking_logic': employee_first_name (str), employee_last_name (str), employee_id (str), origin_city (str), destination_city (str), start_date (str), end_date (str), transport_mode (str), reason (str), y opcionalmente car_type (str).

2. Para consultar solicitudes de viaje por estado:
   - Intenta comprender a qué estado o grupo de estados se refiere el usuario.
   - Llama a la herramienta 'get_travel_requests_by_status' con el argumento: search_term (str).
   - **La herramienta 'get_travel_requests_by_status' devolverá la información formateada como una tabla en texto (Markdown). Cuando recibas su respuesta, preséntala directamente al usuario. Evita re-interpretarla o resumirla a menos que sea un mensaje de error o que no se encuentren resultados. Si es una tabla, muéstrala lo más fielmente posible.**

3. Para actualizar el estado de una solicitud de viaje:
   - Necesitarás el ID de la solicitud ('request_id') y el nuevo estado ('new_status').
   - Pregunta al usuario por estos datos si no los proporciona. Asegúrate de que 'new_status' sea uno de los estados válidos listados arriba.
   - Llama a la herramienta 'update_travel_request_status' con los argumentos: request_id (str) y new_status (str).

Reglas Generales:
- NO inventes información para las herramientas. Pide al usuario cualquier dato que falte.
- Informa al usuario del resultado después de cada llamada a herramienta.
- Sé siempre cortés y profesional.
- La fecha actual es: {datetime.datetime.now().strftime('%Y-%m-%d')}. Considera esto para inferir años si el usuario solo da día y mes para las fechas de viaje.
"""

# --- (Opcional) Pydantic para claridad de argumentos ---
class _TravelBookingArgsSchema(BaseModel):
    employee_first_name: str = Field(description="Nombre del empleado (pila).")
    employee_last_name: str = Field(description="Apellidos del empleado.")
    employee_id: str = Field(description="ID del empleado.")
    origin_city: str = Field(description="Ciudad de origen del viaje.")
    destination_city: str = Field(description="Ciudad de destino del viaje.")
    start_date: str = Field(description="Fecha de inicio del viaje en formato<y_bin_46>MM-DD.")
    end_date: str = Field(description="Fecha de fin del viaje en formato<y_bin_46>MM-DD.")
    transport_mode: str = Field(description="Medio de transporte preferido.")
    reason: str = Field(description="Motivo del viaje.")
    car_type: Optional[str] = Field(default=None, description="Tipo de coche si es 'Coche' (Particular o Alquiler).")

class _GetTravelRequestsArgsSchema(BaseModel):
    search_term: str = Field(description="El estado o término de búsqueda para las solicitudes.")

class _UpdateTravelRequestArgsSchema(BaseModel):
    request_id: str = Field(description="ID de la solicitud a actualizar.")
    new_status: str = Field(description="Nuevo estado para la solicitud.")


# --- Lógica de la Herramienta 1: Registrar Solicitud (Usa DML INSERT) ---
def request_travel_booking_logic(
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
) -> str:
    """Registra una solicitud de reserva de viaje en BigQuery con el nuevo esquema.

    Args:
        employee_first_name (str): Nombre del empleado (pila).
        employee_last_name (str): Apellidos del empleado.
        employee_id (str): ID del empleado.
        origin_city (str): Ciudad de origen del viaje.
        destination_city (str): Ciudad de destino del viaje.
        start_date (str): Fecha de inicio del viaje en formato<y_bin_46>MM-DD.
        end_date (str): Fecha de fin del viaje en formato<y_bin_46>MM-DD.
        transport_mode (str): Medio de transporte preferido (Avión, Tren, Autobús, Coche).
        reason (str): Motivo del viaje.
        car_type (str, optional): Tipo de coche si es 'Coche' (Particular o Alquiler).

    Returns:
        str: Mensaje de confirmación o error.
    """
    try:
        date_format = "%Y-%m-%d"
        current_date_obj = datetime.datetime.now().date()
        start_date_obj = datetime.datetime.strptime(start_date, date_format).date()
        end_date_obj = datetime.datetime.strptime(end_date, date_format).date()

        if start_date_obj < current_date_obj:
            return f"Error en la herramienta: La fecha de inicio '{start_date}' ya ha pasado."
        if end_date_obj < current_date_obj:
             return f"Error en la herramienta: La fecha de fin '{end_date}' ya ha pasado."
        if end_date_obj < start_date_obj:
            return "Error en la herramienta: La fecha de fin no puede ser anterior a la fecha de inicio."
    except ValueError:
        return "Error en la herramienta: El formato de las fechas no es válido. Utiliza<y_bin_46>MM-DD."

    try:
        client = bigquery.Client()
        request_id_val = str(uuid.uuid4())
        current_timestamp = datetime.datetime.now(datetime.timezone.utc)
        initial_status = "Registrada"
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
                bigquery.ScalarQueryParameter("car_type", "STRING", car_type),
                bigquery.ScalarQueryParameter("reason", "STRING", reason),
                bigquery.ScalarQueryParameter("status", "STRING", initial_status),
            ]
        )
        query_job = client.query(query, job_config=job_config)
        query_job.result() 

        if query_job.errors:
            error_messages = "; ".join([str(error["message"]) for error in query_job.errors])
            print(f"[LOG request_travel_booking_logic - ERROR BQ DML]: {error_messages}")
            return f"Error al registrar la solicitud (DML): {error_messages}."
        else:
            if query_job.num_dml_affected_rows is not None and query_job.num_dml_affected_rows > 0:
                full_name = f"{employee_first_name} {employee_last_name}"
                confirmation_message = (
                    f"¡Solicitud registrada (DML)! ID: {request_id_val}. "
                    f"Para {full_name} (ID: {employee_id}) desde {origin_city} a {destination_city} "
                    f"({start_date} a {end_date}), usando {transport_mode}"
                    f"{f' ({car_type})' if car_type and transport_mode.lower() == 'coche' else ''}. Motivo: {reason}."
                )
                print(f"[LOG request_travel_booking_logic]: {confirmation_message}")
                return confirmation_message
            else:
                print(f"[LOG request_travel_booking_logic - ERROR BQ DML]: No se afectaron filas.")
                return "Error al registrar la solicitud: no se insertaron filas."
    except Exception as e:
        print(f"[LOG request_travel_booking_logic - ERROR]: {e}")
        return f"Error técnico al registrar la solicitud: {e}."

# --- Lógica de la Herramienta 2: Consultar Solicitudes por Estado (Devuelve Markdown) ---
def get_travel_requests_by_status(search_term: str) -> str:
    """Consulta solicitudes de viaje. Puede buscar por un estado exacto o interpretar términos comunes como 'pendientes'.
    Devuelve los resultados en formato de tabla Markdown.

    Args:
        search_term (str): El estado exacto (ej. 'Registrada', 'Aprobada') o un término general (ej. 'pendientes').

    Returns:
        str: Una cadena formateada como tabla Markdown con las solicitudes encontradas o un mensaje si no hay ninguna o si ocurre un error.
    """
    try:
        client = bigquery.Client()
        table_ref_str = f"{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_ID}.{BIGQUERY_TABLE_ID}"
        status_conditions = []
        query_params = []
        param_counter = 0
        processed_search_term = search_term.lower().strip()

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
           (not status_conditions and processed_search_term):
            status_conditions = [] 
            query_params = []
            param_counter = 0
            
            param_counter += 1
            status_conditions.append(f"LOWER(status) = LOWER(@status_param_{param_counter})")
            query_params.append(bigquery.ScalarQueryParameter(f"status_param_{param_counter}", "STRING", search_term.strip().capitalize()))

        if not status_conditions:
             print(f"[LOG get_travel_requests_by_status]: Término no interpretado '{search_term}'.")
             return f"No pude interpretar el término de búsqueda de estado: '{search_term}'."

        where_clause = " OR ".join(status_conditions)
        # Seleccionamos campos para la tabla
        query = f"""
            SELECT request_id, employee_first_name, employee_last_name, 
                   destination_city, start_date, end_date, status
            FROM `{table_ref_str}` WHERE {where_clause} ORDER BY timestamp DESC LIMIT 10
        """
        job_config = bigquery.QueryJobConfig(query_parameters=query_params)
        query_job = client.query(query, job_config=job_config)
        results = query_job.result()

        if results.total_rows == 0:
            print(f"[LOG get_travel_requests_by_status]: No se encontraron solicitudes para '{search_term}'.")
            return f"No se encontraron solicitudes de viaje para el término: '{search_term}'."

        headers = ["ID Solicitud", "Empleado", "Destino", "Inicio", "Fin", "Estado"]
        table_md = f"Se encontraron {results.total_rows} solicitudes para '{search_term}':\n\n"
        table_md += "| " + " | ".join(headers) + " |\n"
        table_md += "| " + " | ".join(["---"] * len(headers)) + " |\n"

        for row in results:
            employee_full_name = f"{row.employee_first_name or ''} {row.employee_last_name or ''}".strip()
            row_data = [
                str(row.request_id or "N/A"),
                str(employee_full_name or "N/A"),
                str(row.destination_city or "N/A"),
                str(row.start_date) if row.start_date else "N/A",
                str(row.end_date) if row.end_date else "N/A",
                str(row.status or "N/A")
            ]
            table_md += "| " + " | ".join(row_data) + " |\n"
        
        print(f"[LOG DE HERRAMIENTA get_travel_requests_by_status]: Tabla Markdown generada.")
        return table_md

    except Exception as e:
        print(f"[LOG DE HERRAMIENTA get_travel_requests_by_status - ERROR]: {e}")
        return f"Error técnico al consultar las solicitudes de viaje: {e}."

# --- Lógica de la Herramienta 3: Actualizar Estado de Solicitud ---
def update_travel_request_status(request_id: str, new_status: str) -> str:
    """Actualiza el estado de una solicitud de viaje específica en BigQuery.

    Args:
        request_id (str): ID de la solicitud a actualizar.
        new_status (str): Nuevo estado (ej. 'Aprobada', 'Rechazada', 'Cancelada').

    Returns:
        str: Mensaje de confirmación o error.
    """
    valid_statuses = ["Registrada", "Pendiente de Aprobación", "Aprobada", "Rechazada", "Reservada", "Completada", "Cancelada"]
    capitalized_new_status = new_status.strip().capitalize()
    
    if "pendiente de aprobaci" in new_status.lower():
        capitalized_new_status = "Pendiente de Aprobación"
    elif "registrada" in new_status.lower():
        capitalized_new_status = "Registrada"
    elif "aprobada" in new_status.lower():
        capitalized_new_status = "Aprobada"
    elif "rechazada" in new_status.lower():
        capitalized_new_status = "Rechazada"
    elif "reservada" in new_status.lower():
        capitalized_new_status = "Reservada"
    elif "completada" in new_status.lower():
        capitalized_new_status = "Completada"
    elif "cancelada" in new_status.lower():
        capitalized_new_status = "Cancelada"

    if capitalized_new_status not in valid_statuses:
        return f"Error: '{new_status}' (como '{capitalized_new_status}') no es un estado válido. Válidos: {', '.join(valid_statuses)}."

    try:
        client = bigquery.Client()
        table_ref_str = f"{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_ID}.{BIGQUERY_TABLE_ID}"
        query = f"""
            UPDATE `{table_ref_str}`
            SET status = @new_status_param, timestamp = @current_timestamp_param 
            WHERE request_id = @request_id_param 
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("new_status_param", "STRING", capitalized_new_status),
                bigquery.ScalarQueryParameter("request_id_param", "STRING", request_id),
                bigquery.ScalarQueryParameter("current_timestamp_param", "TIMESTAMP", datetime.datetime.now(datetime.timezone.utc).isoformat())
            ]
        )
        query_job = client.query(query, job_config=job_config)
        query_job.result()

        if query_job.num_dml_affected_rows is not None and query_job.num_dml_affected_rows > 0:
            success_message = f"Solicitud ID '{request_id}' actualizada a '{capitalized_new_status}'."
            print(f"[LOG update_travel_request_status]: {success_message}")
            return success_message
        else:
            not_found_message = f"No se encontró solicitud ID '{request_id}' o el estado ya era '{capitalized_new_status}'."
            print(f"[LOG update_travel_request_status]: {not_found_message}")
            return not_found_message
    except Exception as e:
        error_message = f"Error técnico al actualizar estado de '{request_id}': {e}"
        print(f"[LOG update_travel_request_status - ERROR]: {error_message}")
        return error_message

# --- Definición del Agente ---
company_travel_agent = LlmAgent(
    name="CompanyTravelAgent",
    description="Agente para gestionar solicitudes de viaje: registrar, consultar y actualizar estados en BigQuery.",
    instruction=TRAVEL_AGENT_INSTRUCTION,
    model=MODEL_ID,
    tools=[
        request_travel_booking_logic,
        get_travel_requests_by_status,
        update_travel_request_status
    ]
)

# ADK buscará esta variable 'agent' por defecto en el paquete.
agent = company_travel_agent