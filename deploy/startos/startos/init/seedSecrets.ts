import { utils } from '@start9labs/start-sdk'
import { storeJson } from '../fileModels/store.json'
import { sdk } from '../sdk'
import { apiKeyPrefix, secretCharset } from '../utils'

const secret = (len: number) =>
  utils.getDefaultString({ charset: secretCharset, len })

export const newApiKey = () => `${apiKeyPrefix}${secret(40)}`

export const seedSecrets = sdk.setupOnInit(async (effects, kind) => {
  await storeJson.merge(effects, {})

  if (kind !== 'install') return

  await storeJson.merge(effects, {
    apiKey: newApiKey(),
    sessionSecret: secret(64),
    internalToken: secret(48),
  })
})
