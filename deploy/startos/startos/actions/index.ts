import { sdk } from '../sdk'
import { configureJitsi } from './configureJitsi'
import { configureNotesLlm } from './configureNotesLlm'
import { configureProfile } from './configureProfile'
import { configureRetention } from './configureRetention'
import { configureTranscription } from './configureTranscription'
import { getApiCredentials } from './getApiCredentials'
import { rotateApiKey } from './rotateApiKey'
import { setAdminPassword } from './setAdminPassword'

export const actions = sdk.Actions.of()
  .addAction(setAdminPassword)
  .addAction(getApiCredentials)
  .addAction(rotateApiKey)
  .addAction(configureTranscription)
  .addAction(configureNotesLlm)
  .addAction(configureJitsi)
  .addAction(configureRetention)
  .addAction(configureProfile)
