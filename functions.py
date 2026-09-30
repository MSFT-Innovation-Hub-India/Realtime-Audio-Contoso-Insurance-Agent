import struct
from contextlib import closing

import pyodbc
from atlassian import Jira
from azure.search.documents import SearchClient
from chainlit.logger import logger

from azure_auth import AZURE_SQL_SCOPE, CachedTokenCredential, get_access_token
from envconfig import DefaultConfig

# pyodbc connection attribute used to pass a Microsoft Entra access token to the ODBC driver
SQL_COPT_SS_ACCESS_TOKEN = 1256


def perform_search_based_qna(query: str) -> str:
    """Retrieves grounding context from Azure AI Search using Microsoft Entra ID (managed identity)."""
    logger.info(f"Searching the knowledge base for: {query}")
    client = SearchClient(
        endpoint=DefaultConfig.ai_search_url,
        index_name=DefaultConfig.ai_index_name,
        credential=CachedTokenCredential(),
    )
    try:
        results = client.search(
            search_text=query,
            query_type="semantic",
            semantic_configuration_name=DefaultConfig.ai_semantic_config,
            top=2,
        )
        response_docs = ""
        for result in results:
            response_docs += (
                " --- Document context start ---"
                + result["content"]
                + "\n ---End of Document ---\n"
            )
        return response_docs or "No relevant information was found in the knowledge base."
    except Exception as e:
        logger.error(f"Error querying Azure AI Search: {e}")
        return "We had an issue searching the knowledge base. Please try again in some time."
    finally:
        client.close()


def _init_jira_connection() -> Jira:
    jira = Jira(
        url=DefaultConfig.attlassian_url,
        username=DefaultConfig.attlassian_user_name,
        password=DefaultConfig.attlassian_api_key,
        cloud=True,
    )
    return jira


def _adf_to_text(value) -> str:
    """Jira Cloud's enhanced JQL search (REST v3) returns rich-text fields in Atlassian Document Format."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_adf_to_text(v) for v in value)
    if isinstance(value, dict):
        if value.get("type") == "text":
            return value.get("text", "")
        if value.get("type") == "hardBreak":
            return "\n"
        text = _adf_to_text(value.get("content"))
        if value.get("type") in ("paragraph", "heading", "listItem"):
            text += "\n"
        return text
    return str(value)


def get_grievance_status_def(grievance_id) -> str:
    try:
        grievance_id = int(grievance_id)
    except (TypeError, ValueError):
        return "The grievance id should be a number. Can you please verify your input again?"

    jql = f'project = "{DefaultConfig.grievance_project_key}" AND id = {grievance_id}'
    try:
        response_message = _init_jira_connection().jql(jql)
        issues = response_message.get("issues") or []
        if not issues:
            return "sorry, we could not locate a grievance with this ID. Can you please verify your input again?"

        fields = issues[0]["fields"]
        priority = (fields.get("priority") or {}).get("name", "not set")
        status = fields["status"]["name"]
        response = (
            "\n Here is the updated status of your grievance.\ngrievance_id : "
            + issues[0]["id"]
            + "\npriority : "
            + priority
            + "\nstatus : "
            + status
            + "\ngrievance description : "
            + str(_adf_to_text(fields.get("description")).strip())
        )
        if fields.get("duedate"):
            response += "\ndue date : " + fields["duedate"]
        else:
            response += "\ndue date : not assigned by the system yet."
        return response
    except Exception as e:
        logger.error(f"Error retrieving the grievance: {e}")
        return "We had an issue retrieving your grievance status. Please check back in some time"


def register_user_grievance_def(grievance_category: str, grievance_description: str) -> str:
    try:
        issue_details = {
            "project": {"key": DefaultConfig.grievance_project_key},
            "summary": grievance_category,
            "description": grievance_description,
            "issuetype": {"name": DefaultConfig.grievance_type},
        }
        response = _init_jira_connection().create_issue(fields=issue_details)
        logger.info("Grievance registered successfully")
        return (
            "We are sorry about the issue you are facing. We have registered a grievance with id "
            + response["id"]
            + " to track it to closure. Please quote that in your future communications with us"
        )
    except Exception as e:
        logger.error(f"Error registering the grievance issue: {e}")
        return "We had an issue registering your grievance. Please check back in some time"


def _get_sql_connection() -> pyodbc.Connection:
    """Opens a connection to Azure SQL Database authenticated with a Microsoft Entra access token."""
    token = get_access_token(AZURE_SQL_SCOPE).encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token)}s", len(token), token)
    connection_string = (
        f"Driver={{{DefaultConfig.az_db_driver}}};"
        f"Server=tcp:{DefaultConfig.az_db_server},1433;"
        f"Database={DefaultConfig.az_db_database};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    return pyodbc.connect(connection_string, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})


def get_claim_status_summary(**_model_arguments) -> str:
    # The patient is always the current user, never a name chosen by the model, so a user
    # can't talk the assistant into disclosing another patient's claims.
    user_name = DefaultConfig.customer_name
    logger.info(f"Fetching claim status summary for patient {user_name}")
    query = """
        SELECT
            Claim_ID,
            Service_Type,
            Claim_Amount,
            Claim_Status,
            Reason_for_Denial,
            Patient_Name
        FROM Health_Insurance_Claims
        WHERE Patient_Name = ?;
    """
    try:
        with closing(_get_sql_connection()) as connection:
            cursor = connection.cursor()
            cursor.execute(query, user_name)
            rows = cursor.fetchall()

        if not rows:
            return f"No claims found for patient: {user_name}"

        table = (
            "| Claim ID | Service Type | Claim Amount | Claim Status | Reason for Denial | Patient Name |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
        )
        for row in rows:
            reason_for_denial = row[4] if row[4] is not None else "N/A"
            table += f"| {row[0]} | {row[1]} | ${row[2]} | {row[3]} | {reason_for_denial} | {row[5]} |\n"
        return table
    except Exception as e:
        logger.error(f"Error in database query execution: {e}")
        return "We had an issue retrieving your claim status. Please check back in some time"
