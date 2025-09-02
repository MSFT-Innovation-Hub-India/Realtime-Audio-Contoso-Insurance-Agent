# gpt-4o Realtime API based Sample AI Assistant for Edu vertical

This sample demonstrates the use of the Realtime API to build a highly interactive AI Assistant that one could communicate with using both speech/audio and text.
The life cycle events in this Web socket API are quite complex, and this sample helps one to understand them.

The sample uses the following skills
- Azure AI Search to help answers to questions from students in the subjects Physics, Chemistry and Accountancy
- Azure SQL Database to get the summary of the marks obtained in the Exams
- Jira Cloud to raise grievances


### Features:

- User can voice in their question, and the AI Assistant responds back through audio. An audio transcript of the response also gets generated, which gets displayed in the chat window
- The user could also type in their question. The AI Assistant would respond in a combination of audio and audio transcript
- Uses Chainlit for the UI

### Installation

Create a virtual environment and install the libraries

**Note:**  Install only the versions of chainlit and pydantic mentioned in the requirements.txt.

### Configuration


Create a .env file with the following configurations

```
az_openai_key = ""
az_open_ai_endpoint_name = "xxxxxxx"
az_openai_api_version = "2024-10-01-preview"
model_name="gpt-4o-realtime-preview"

attlassian_api_key = ''
attlassian_user_name = ''
attlassian_url = 'https://nnnnnnn.atlassian.net/'

ai_search_url = "https://nnnnnn.search.windows.net"
ai_search_key = ""
ai_index_name = "vector-edu-docs-idx"
ai_semantic_config = "vector-edu-docs-idx-semantic-configuration"

grievance_project_key = 'CN'
grievance_type = 'Task'
grievance_project_name = 'ContosoGamingSupport'

az_db_server = "xxxxxx.database.windows.net" 
az_db_database = "cdcsampledb" 
az_db_username = "" 
az_db_password = ""

```

[Note: if the azure OpenAI endpoint url is https://mydemogpt4.openai.azure.com/, then the value to set, for az_open_ai_endpoint_name in the config above, is mydemogpt4]



### Run the application

```
chainlit run app.py -w
```

### Limitations in the App

The following events are returned by the server asynchronously, and not necessarily in the right order
- transcript of the user audio input (*event conversation.item.input_audio_transcription.completed*) and
- the transcript of the server response to the user input (*event response.audio_transcript.delta*)

Hence, in the chat window, the transcript from the server response would get populated before the user query itself, and the sequencing in the UI could get awry.
I have handled this issue in a custom way in the UI, but it is not perfect.

I have tried to use the response.text.delta event from the server, to get the input audio transcript, but these events don't seem to be getting raised at all.
Will update this repo once I figure how that could be handled.