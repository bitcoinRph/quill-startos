import { utils } from '@start9labs/start-sdk'
import { storeJson } from '../fileModels/store.json'
import { i18n } from '../i18n'
import { sdk } from '../sdk'
import { secretCharset } from '../utils'

const { InputSpec, Value } = sdk

const passwordLength = 24

export const inputSpec = InputSpec.of({
  password: Value.text({
    name: i18n('Password'),
    description: i18n(
      'Leave empty to generate a random password. Use at least 12 characters if you choose your own.',
    ),
    required: false,
    default: null,
    masked: true,
    minLength: 12,
    generate: { charset: secretCharset, len: passwordLength },
  }),
})

export const setAdminPassword = sdk.Action.withInput(
  'set-admin-password',

  async () => ({
    name: i18n('Set Admin Password'),
    description: i18n(
      'Set or reset the password that signs in to the Quill web app. It is applied when Quill restarts.',
    ),
    warning: null,
    allowedStatuses: 'any',
    group: null,
    visibility: 'enabled',
  }),

  inputSpec,

  async () => ({ password: null }),

  async ({ effects, input }) => {
    const password =
      input.password?.trim() ||
      utils.getDefaultString({ charset: secretCharset, len: passwordLength })

    await storeJson.merge(effects, { adminPassword: password })

    return {
      version: '1',
      title: i18n('Admin Password'),
      message: i18n(
        'Use this password on the Quill sign-in page. It is applied when Quill restarts.',
      ),
      result: {
        type: 'single',
        value: password,
        masked: true,
        copyable: true,
        qr: false,
      },
    }
  },
)
