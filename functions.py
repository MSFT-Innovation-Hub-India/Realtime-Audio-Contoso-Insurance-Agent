from chainlit.logger import logger
from envconfig import DefaultConfig
import pyodbc
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from atlassian import Jira

def perform_search_based_qna(query):
    logger.info("calling search to get context for the response ....")
    credential = AzureKeyCredential(DefaultConfig.ai_search_key)
    client = SearchClient(
        endpoint=DefaultConfig.ai_search_url,
        index_name=DefaultConfig.ai_index_name,
        credential=credential,
    )
    response = client.search(
        search_text=query,
        query_type="semantic",
        semantic_configuration_name=DefaultConfig.ai_semantic_config,
    )
    response_docs = ""
    counter = 0
    results = list(response)
    for result in results:
        # logger.info(f"search result:  {result}  ")
        logger.info(f"search result from document:  {result['content']}  ")
        response_docs += (
            " --- Document context start ---"
            + result["content"]
            + "\n ---End of Document ---\n"
        )
        counter += 1
        if counter == 2:
            break
    logger.info("***********  calling LLM now ....***************")
    return response_docs

def init_jira_connection():
    l_jira = None
    try:
        l_jira = Jira(
            url=DefaultConfig.attlassian_url,
            username=DefaultConfig.attlassian_user_name,
            password=DefaultConfig.attlassian_api_key,
        )
        l_jira.myself()
        print("Connected to Jira ticketing system....")
        logger.info("Connected to Jira ticketing system....")
        return l_jira
    except Exception as e:
        logger.error(f"Error connecting to Jira: {e}")
        logger.error("Exiting the program....")
        exit(1)

def get_grievance_status_def(grievance_id):
    response_message = ""
    response = ""
    JQL = "project = " + DefaultConfig.grievance_project_name + " AND id = " + str(grievance_id)
    l_jira = init_jira_connection()
    try:
        response_message = l_jira.jql(JQL)
        logger.info("Issue status retrieved successfully!")
        logger.info("grievance status response .. ", response_message)
        if response_message["issues"]:
            response = (
                "\n Here is the updated status of your grievance.\ngrievance_id : "
                + response_message["issues"][0]["id"]
            )
            response += (
                "\npriority : "
                + response_message["issues"][0]["fields"]["priority"]["name"]
            )
            response += (
                "\nstatus : "
                + response_message["issues"][0]["fields"]["status"]["statusCategory"]["key"]
            )
            response += (
                "\ngrievance description : "
                + response_message["issues"][0]["fields"]["description"]
            )
            if response_message["issues"][0]["fields"]["duedate"]:
                response += (
                    "\ndue date : " + response_message["issues"][0]["fields"]["duedate"]
                )
            else:
                response += "\ndue date : not assigned by the system yet."
        else:
            response = "sorry, we could not locate a grievance with this ID. Can you please verify your input again?"
    except Exception as e:
        logger.error(f"Error retrieving the grievance: {e.args[0]}")
        response = "We had an issue retrieving your grievance status. Please check back in some time"
    return response

def register_user_grievance_def(grievance_category, grievance_description):
    response_message = ""
    try:
        issue_details = {
            "project": {"key": DefaultConfig.grievance_project_key},
            "summary": grievance_category,
            "description": grievance_description,
            "issuetype": {"name": "Task"},
        }
        l_jira = init_jira_connection()
        response = l_jira.create_issue(fields=issue_details)
        response_message = (
            "We are sorry about the issue you are facing. We have registered a grievance with id "
            + response["id"]
            + " to track it to closure. Please quote that in your future communications with us"
        )
        logger.info("Issue created successfully!")
    except Exception as e:
        logger.error(f"Error registering the grievance issue: {e.args[0]}")
        response_message = "We had an issue registering your grievance. Please check back in some time"
    return response_message

def get_claim_status_summary(user_name):
    response_message = ""
    cursor = None
    logger.info(f"calling the database to fetch claim status summary for patient {user_name}")
    l_connection = None
    try:
        l_connection = pyodbc.connect(
            "Driver={ODBC Driver 18 for SQL Server};SERVER="
            + DefaultConfig.az_db_server
            + ";DATABASE="
            + DefaultConfig.az_db_database
            + ";UID="
            + DefaultConfig.az_db_username
            + ";PWD="
            + DefaultConfig.az_db_password
        )
        cursor = l_connection.cursor()
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
        cursor.execute(query, user_name)
        table_header = "| Claim ID | Service Type | Claim Amount | Claim Status | Reason for Denial | Patient Name |\n"
        table_separator = "| --- | --- | --- | --- | --- | --- |\n"
        table_rows = ""
        for row in cursor:
            reason_for_denial = row[4] if row[4] is not None else "N/A"
            table_rows += f"| {row[0]} | {row[1]} | ${row[2]} | {row[3]} | {reason_for_denial} | {row[5]} |\n"
        
        if table_rows:
            markdown_table = table_header + table_separator + table_rows
        else:
            markdown_table = f"No claims found for patient: {user_name}"
            
        l_connection.close()
        return markdown_table
    except Exception as e:
        logger.error(f"Error in database query execution: {e}")
        response_message = "We had an issue retrieving your claim status. Please check back in some time"
        return response_message


def init_database_connection() -> any:
    l_connection = None
    try:
        l_connection = pyodbc.connect(
            "Driver={ODBC Driver 18 for SQL Server};SERVER="
            + DefaultConfig.az_db_server
            + ";DATABASE="
            + DefaultConfig.az_db_database
            + ";UID="
            + DefaultConfig.az_db_username
            + ";PWD="
            + DefaultConfig.az_db_password
        )
        print("Connected to the database....")
        logger.info("Connected to the database....")
        return l_connection
    except Exception as e:
        logger.error(f"Error connecting to the database: {e}")
        logger.error("Exiting the program....")
        exit(1)

available_functions = {
    "perform_search_based_qna": perform_search_based_qna,
    "get_claim_status_summary": get_claim_status_summary,
    "get_grievance_status_def": get_grievance_status_def,
    "register_user_grievance_def": register_user_grievance_def,
}
