import { storeJson } from '../fileModels/store.json'
import { newApiKey } from '../init/seedSecrets'
import { i18n } from '../i18n'
import { sdk } from '../sdk'

export const rotateApiKey = sdk.Action.withoutInput(
  'rotate-api-key',

  async () => ({
    name: i18n('Rotate API Key'),
    description: i18n(
      'Generate a new service API key. Agents and scripts that use the old key stop working until you update them. API keys created in the Quill web app are not affected.',
    ),
    warning: i18n(
      'The current API key stops working as soon as Quill restarts.',
    ),
    allowedStatuses: 'any',
    group: null,
    visibility: 'enabled',
  }),

  async ({ effects }) => {
    const apiKey = newApiKey()
    await storeJson.merge(effects, { apiKey })

    return {
      version: '1',
      title: i18n('New API Key'),
      message: i18n(
        'Quill restarts to apply it. Run Get API Credentials for ready-made setup commands.',
      ),
      result: {
        type: 'single',
        value: apiKey,
        masked: true,
        copyable: true,
        qr: false,
      },
    }
  },
)
