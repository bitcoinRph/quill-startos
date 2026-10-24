import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'

export const current = VersionInfo.of({
  version: '0.1.3:0',
  releaseNotes: {
    en_US:
      'First StartOS release of the Quill server: web UI, REST API and MCP endpoint, Parakeet v3 speech engine built in, Jitsi notetaker bot, browser recorder and file upload, with optional OpenWhispr and Ollama services.',
    es_ES:
      'Primera versión de StartOS del servidor Quill: interfaz web, API REST y endpoint MCP, motor de voz Parakeet v3 integrado, bot de notas para Jitsi, grabadora en el navegador y subida de archivos, con servicios opcionales OpenWhispr y Ollama.',
    de_DE:
      'Erste StartOS-Version des Quill-Servers: Weboberfläche, REST-API und MCP-Endpunkt, eingebaute Parakeet-v3-Spracherkennung, Jitsi-Notizen-Bot, Browser-Recorder und Datei-Upload, mit optionalen OpenWhispr- und Ollama-Diensten.',
    pl_PL:
      'Pierwsze wydanie serwera Quill dla StartOS: interfejs webowy, API REST i punkt końcowy MCP, wbudowany silnik mowy Parakeet v3, bot do notatek dla Jitsi, rejestrator w przeglądarce i wgrywanie plików, z opcjonalnymi usługami OpenWhispr i Ollama.',
    fr_FR:
      'Première version StartOS du serveur Quill : interface web, API REST et point de terminaison MCP, moteur vocal Parakeet v3 intégré, bot de notes Jitsi, enregistreur dans le navigateur et import de fichiers, avec les services facultatifs OpenWhispr et Ollama.',
  },
  migrations: {
    up: async ({ effects }) => {},
    down: IMPOSSIBLE,
  },
})
