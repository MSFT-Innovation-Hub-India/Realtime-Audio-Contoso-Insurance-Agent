# Contoso Insurance Voice Agent

### 🎙️ Powered by Azure Voice Live in Microsoft Foundry

A speech-to-speech assistant built on the [Azure Voice Live API](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live) with a GPT-Realtime model. It speaks with an Azure HD neural voice and uses tools to answer:

- **Policy and product questions**: Azure AI Search knowledge base
- **Claim status**: Azure SQL Database
- **Grievances** (register and track): Atlassian Jira Cloud

Azure resources are accessed with **managed identity** (Microsoft Entra ID), with no keys.

**How to use**
1. Click the microphone icon (or press `P`) and speak. You can talk over the assistant to interrupt it.
2. Or type a question. Replies are spoken while the microphone is on.
3. Click **Show configuration** (or use the settings switch) to see the models, voice and Azure resources in use. Click **Hide configuration** to collapse the panel.
