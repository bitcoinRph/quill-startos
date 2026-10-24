export const short = {
  en_US:
    'Private meeting recorder and transcriber with a Jitsi note-taking bot',
  es_ES:
    'Grabador y transcriptor de reuniones privado con un bot de notas para Jitsi',
  de_DE: 'Privater Meeting-Recorder und Transkriber mit Notizen-Bot für Jitsi',
  pl_PL:
    'Prywatny rejestrator i transkrybent spotkań z botem do notatek dla Jitsi',
  fr_FR:
    'Enregistreur et transcripteur de réunions privé avec un bot de notes pour Jitsi',
}

export const long = {
  en_US:
    'Quill records meetings and transcribes them on your own server with NVIDIA Parakeet, so audio and transcripts never leave it. A headless notetaker bot joins meetings on your own Jitsi server and transcribes each participant on a separate track. You can also record from a browser tab and microphone, or upload audio files. Transcripts are searchable and can be exported as Markdown, text, SRT or VTT. An MCP endpoint and a REST API let Claude Code, Codex, Hermes Agent or OpenClaw read transcripts, follow a meeting live and write notes back. Optional OpenWhispr and Ollama services on the same server can replace the built-in speech engine and write notes with a local model.',
  es_ES:
    'Quill graba reuniones y las transcribe en tu propio servidor con NVIDIA Parakeet, de modo que el audio y las transcripciones nunca salen de él. Un bot de notas sin interfaz entra en las reuniones de tu propio servidor Jitsi y transcribe a cada participante en una pista aparte. También puedes grabar desde una pestaña del navegador y el micrófono, o subir archivos de audio. Las transcripciones se pueden buscar y exportar como Markdown, texto, SRT o VTT. Un endpoint MCP y una API REST permiten que Claude Code, Codex, Hermes Agent u OpenClaw lean transcripciones, sigan una reunión en directo y escriban las notas. Los servicios opcionales OpenWhispr y Ollama del mismo servidor pueden sustituir el motor de voz integrado y redactar notas con un modelo local.',
  de_DE:
    'Quill nimmt Meetings auf und transkribiert sie mit NVIDIA Parakeet auf Ihrem eigenen Server, sodass Audio und Transkripte ihn nie verlassen. Ein Notizen-Bot ohne Oberfläche tritt Meetings auf Ihrem eigenen Jitsi-Server bei und transkribiert jeden Teilnehmer auf einer eigenen Spur. Sie können auch aus einem Browser-Tab und dem Mikrofon aufnehmen oder Audiodateien hochladen. Transkripte sind durchsuchbar und als Markdown, Text, SRT oder VTT exportierbar. Ein MCP-Endpunkt und eine REST-API lassen Claude Code, Codex, Hermes Agent oder OpenClaw Transkripte lesen, ein Meeting live verfolgen und Notizen zurückschreiben. Optionale OpenWhispr- und Ollama-Dienste auf demselben Server können die eingebaute Spracherkennung ersetzen und Notizen mit einem lokalen Modell schreiben.',
  pl_PL:
    'Quill nagrywa spotkania i transkrybuje je na Twoim własnym serwerze za pomocą NVIDIA Parakeet, więc dźwięk i transkrypcje nigdy go nie opuszczają. Bot do notatek bez interfejsu dołącza do spotkań na Twoim własnym serwerze Jitsi i transkrybuje każdego uczestnika na osobnej ścieżce. Możesz też nagrywać z karty przeglądarki i mikrofonu lub wgrywać pliki audio. Transkrypcje można przeszukiwać i eksportować jako Markdown, tekst, SRT lub VTT. Punkt końcowy MCP i API REST pozwalają Claude Code, Codex, Hermes Agent lub OpenClaw czytać transkrypcje, śledzić spotkanie na żywo i zapisywać notatki. Opcjonalne usługi OpenWhispr i Ollama na tym samym serwerze mogą zastąpić wbudowany silnik mowy i pisać notatki lokalnym modelem.',
  fr_FR:
    'Quill enregistre les réunions et les transcrit sur votre propre serveur avec NVIDIA Parakeet, de sorte que l’audio et les transcriptions ne le quittent jamais. Un bot de notes sans interface rejoint les réunions de votre propre serveur Jitsi et transcrit chaque participant sur une piste séparée. Vous pouvez aussi enregistrer depuis un onglet du navigateur et le micro, ou importer des fichiers audio. Les transcriptions sont consultables et exportables en Markdown, texte, SRT ou VTT. Un point de terminaison MCP et une API REST permettent à Claude Code, Codex, Hermes Agent ou OpenClaw de lire les transcriptions, de suivre une réunion en direct et d’écrire les notes. Les services facultatifs OpenWhispr et Ollama du même serveur peuvent remplacer le moteur vocal intégré et rédiger les notes avec un modèle local.',
}

export const openwhisprDependency = {
  en_US:
    'Optional. Transcribes with the OpenWhispr service on this server (99 languages) when Configure Transcription selects it. Not needed for the built-in Parakeet engine.',
  es_ES:
    'Opcional. Transcribe con el servicio OpenWhispr de este servidor (99 idiomas) cuando Configurar transcripción lo selecciona. No hace falta para el motor Parakeet integrado.',
  de_DE:
    'Optional. Transkribiert mit dem OpenWhispr-Dienst auf diesem Server (99 Sprachen), wenn „Transkription konfigurieren“ ihn auswählt. Für die eingebaute Parakeet-Engine nicht nötig.',
  pl_PL:
    'Opcjonalne. Transkrybuje usługą OpenWhispr na tym serwerze (99 języków), gdy wybierze ją Konfiguruj transkrypcję. Wbudowany silnik Parakeet jej nie potrzebuje.',
  fr_FR:
    'Facultatif. Transcrit avec le service OpenWhispr de ce serveur (99 langues) lorsque Configurer la transcription le sélectionne. Inutile pour le moteur Parakeet intégré.',
}

export const ollamaDependency = {
  en_US:
    'Optional. Writes meeting notes with a local model on the Ollama service of this server when Configure Notes LLM selects it. Not needed to record or transcribe.',
  es_ES:
    'Opcional. Redacta las notas de la reunión con un modelo local del servicio Ollama de este servidor cuando Configurar LLM de notas lo selecciona. No hace falta para grabar ni transcribir.',
  de_DE:
    'Optional. Schreibt Meeting-Notizen mit einem lokalen Modell des Ollama-Dienstes auf diesem Server, wenn „Notizen-LLM konfigurieren“ ihn auswählt. Zum Aufnehmen und Transkribieren nicht nötig.',
  pl_PL:
    'Opcjonalne. Pisze notatki ze spotkań lokalnym modelem usługi Ollama na tym serwerze, gdy wybierze ją Konfiguruj LLM do notatek. Do nagrywania i transkrypcji nie jest potrzebne.',
  fr_FR:
    'Facultatif. Rédige les notes de réunion avec un modèle local du service Ollama de ce serveur lorsque Configurer le LLM des notes le sélectionne. Inutile pour enregistrer ou transcrire.',
}
