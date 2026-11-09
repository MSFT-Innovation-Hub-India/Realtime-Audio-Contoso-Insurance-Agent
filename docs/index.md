---
layout: default
title: Real-Time Insurance Voice Agent with Azure Voice Live API and gpt-realtime-2.1
headline: Real-Time Insurance Voice Agent with Azure Voice Live API and gpt-realtime-2.1
description: Speech-to-speech insurance voice agent on Azure Voice Live API and gpt-realtime-2.1, with Azure AI Search, Azure SQL and Jira function calling.
---

## The business problem

Insurance customers want answers about coverage, claims and complaints without waiting on hold or digging through a portal. A voice assistant can handle these everyday requests around the clock, as long as it sounds natural, answers from trusted company data, and doesn't get confused by a real-world environment: coughs, background chatter and people talking over it.

## What this solution does

**Contoso Insurance Voice Agent** is an open-source, speech-to-speech AI assistant. A customer talks (or types) and the assistant replies in a natural Azure HD neural voice, while a live transcript appears in a web UI. In one conversation the customer can:

- **Ask about plans, coverage and the claim process.** Answers come from an Azure AI Search knowledge base, so the assistant stays grounded in Contoso's documents.
- **Check the status of their claims.** Claims are read from Azure SQL Database.
- **Raise a complaint and track it.** Grievances are created and looked up as Jira issues, and the assistant reads back the grievance id.

## Why it works well in real conversations

- **Ignores random sounds.** Noise suppression, echo cancellation, semantic voice activity detection and a client-side interrupt debounce mean a cough, a click or a filler word ("uh", "hmm") doesn't stop the assistant.
- **Stops only when you actually say something.** Real barge-in still works: talk over the assistant and it stops, and its memory is trimmed to what you heard.
- **Handles Hindi and other languages.** The persona keeps a consistent voice and first-person grammar in gendered languages.
- **Keyless and secure.** Azure AI Search, Azure SQL and the Voice Live API are accessed with Microsoft Entra ID and managed identity. Only the Jira token is a secret.

## How it works

For developers, the stack is:

| Layer | Technology |
| --- | --- |
| Speech-to-speech model | `gpt-realtime-2.1` through the **Azure Voice Live API** in Microsoft Foundry |
| Web UI | [Chainlit](https://docs.chainlit.io) 2.x with live transcript and microphone support |
| Function calling | Client-side tools for **Azure AI Search**, **Azure SQL Database** and **Atlassian Jira** |
| Authentication | `DefaultAzureCredential` and managed identity (no Azure API keys) |
| Hosting | Docker image on **Azure Container Apps**, deployed with one idempotent PowerShell script |

![Solution architecture of the Azure Voice Live insurance voice agent: Chainlit UI, Azure Voice Live API, Azure AI Search, Azure SQL and Jira]({{ '/assets/architecture.png' | relative_url }})

The Chainlit app streams microphone audio over a WebSocket to the Voice Live API. When the model decides it needs data, it calls a tool; the app runs the query against Azure AI Search, Azure SQL or Jira and returns the result, and the model speaks the answer.

## Get the code

- [Browse the repository on GitHub]({{ site.repo_url }})
- [Step-by-step setup and run instructions]({{ site.repo_url }}#how-to-run)
- [Architecture, configuration reference and tuning guide]({{ site.repo_url }}#architecture)

<p class="cta"><a class="btn primary" href="{{ site.repo_url }}">Get the code on GitHub</a></p>
