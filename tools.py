from functions import (
    get_claim_status_summary,
    get_grievance_status_def,
    perform_search_based_qna,
    register_user_grievance_def,
)

tools_list = [
    {
        "type": "function",
        "name": "perform_search_based_qna",
        "description": "call this function to respond to general user queries on the Insurance products and policies offered by Contoso Insurance",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "get_claim_status_summary",
        "description": "retrieve the claim status summary of the current user (the patient you are interacting with)",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "get_grievance_status_def",
        "description": "fetch real time grievance status for a grievance id",
        "parameters": {
            "type": "object",
            "properties": {
                "grievance_id": {
                    "type": "number",
                    "description": "The grievance id of the user registered in the Grievance System",
                }
            },
            "required": ["grievance_id"],
        },
    },
    {
        "type": "function",
        "name": "register_user_grievance_def",
        "description": "register a grievance, or complaint or issue from the Customer related to their insurance policy, or experience using the App, or the service offered, payment issues, their claims, etc",
        "parameters": {
            "type": "object",
            "properties": {
                "grievance_category": {
                    "type": "string",
                    "enum": [
                        "payment issues",
                        "premium over charge",
                        "app experience issues",
                        "claims issues",
                        "other issues",
                    ],
                },
                "grievance_description": {
                    "type": "string",
                    "description": "The detailed description of the grievance faced by the user",
                },
            },
            "required": ["grievance_category", "grievance_description"],
        },
    },
]


available_functions = {
    "perform_search_based_qna": perform_search_based_qna,
    "get_claim_status_summary": get_claim_status_summary,
    "get_grievance_status_def": get_grievance_status_def,
    "register_user_grievance_def": register_user_grievance_def,
}
