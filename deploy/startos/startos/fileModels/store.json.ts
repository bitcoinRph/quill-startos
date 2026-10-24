import { FileHelper, z } from '@start9labs/start-sdk'
import { sdk } from '../sdk'
import {
  defaultBotAloneMinutes,
  defaultBotMaxMinutes,
  defaultBotName,
  defaultOllamaAsrModel,
  defaultRemoteAsrModel,
  defaultSpeakerName,
} from '../utils'

export const transcriptionEngines = [
  'parakeet-v3',
  'parakeet-v2',
  'openwhispr',
  'ollama',
  'custom',
] as const

export const notesSources = ['none', 'ollama', 'custom'] as const

const openwhisprShape = z.object({
  token: z.string().catch(''),
  model: z.string().catch(defaultRemoteAsrModel),
})

const ollamaAsrShape = z.object({
  model: z.string().catch(defaultOllamaAsrModel),
})

const customAsrShape = z.object({
  url: z.string().catch(''),
  key: z.string().catch(''),
  model: z.string().catch(defaultRemoteAsrModel),
})

const transcriptionShape = z.object({
  engine: z.enum(transcriptionEngines).catch('parakeet-v3'),
  threads: z.number().int().min(0).max(64).catch(0),
  openwhispr: openwhisprShape.catch(() => openwhisprShape.parse({})),
  ollama: ollamaAsrShape.catch(() => ollamaAsrShape.parse({})),
  custom: customAsrShape.catch(() => customAsrShape.parse({})),
})

const customLlmShape = z.object({
  baseUrl: z.string().catch(''),
  key: z.string().catch(''),
  model: z.string().catch(''),
})

const notesShape = z.object({
  source: z.enum(notesSources).catch('none'),
  autoNotes: z.boolean().catch(false),
  ollamaModel: z.string().catch(''),
  custom: customLlmShape.catch(() => customLlmShape.parse({})),
})

const jitsiShape = z.object({
  domains: z.array(z.string()).catch([]),
  botName: z.string().catch(defaultBotName),
  announce: z.boolean().catch(true),
  maxMinutes: z.number().int().catch(defaultBotMaxMinutes),
  aloneMinutes: z.number().int().catch(defaultBotAloneMinutes),
})

const retentionShape = z.object({
  audioDays: z.number().int().min(0).catch(0),
  compressAudio: z.boolean().catch(true),
})

const profileShape = z.object({
  name: z.string().catch(defaultSpeakerName),
  email: z.string().catch(''),
})

const shape = z.object({
  adminPassword: z.string().catch(''),
  apiKey: z.string().catch(''),
  sessionSecret: z.string().catch(''),
  internalToken: z.string().catch(''),
  transcription: transcriptionShape.catch(() => transcriptionShape.parse({})),
  notes: notesShape.catch(() => notesShape.parse({})),
  jitsi: jitsiShape.catch(() => jitsiShape.parse({})),
  retention: retentionShape.catch(() => retentionShape.parse({})),
  profile: profileShape.catch(() => profileShape.parse({})),
})

export type Store = z.infer<typeof shape>

export const storeJson = FileHelper.json(
  { base: sdk.volumes.main, subpath: './store.json' },
  shape,
)
