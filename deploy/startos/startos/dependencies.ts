import { storeJson } from './fileModels/store.json'
import { sdk } from './sdk'
import {
  ollamaHealthCheck,
  ollamaVersionRange,
  openwhisprHealthCheck,
  openwhisprVersionRange,
} from './utils'

export const setDependencies = sdk.setupDependencies(async ({ effects }) => {
  const store = await storeJson.read().const(effects)

  const useOpenwhispr = store?.transcription.engine === 'openwhispr'
  const useOllama =
    store?.notes.source === 'ollama' || store?.transcription.engine === 'ollama'

  return {
    ...(useOpenwhispr && {
      openwhispr: {
        kind: 'running' as const,
        versionRange: openwhisprVersionRange,
        healthChecks: [openwhisprHealthCheck],
      },
    }),
    ...(useOllama && {
      ollama: {
        kind: 'running' as const,
        versionRange: ollamaVersionRange,
        healthChecks: [ollamaHealthCheck],
      },
    }),
  }
})
