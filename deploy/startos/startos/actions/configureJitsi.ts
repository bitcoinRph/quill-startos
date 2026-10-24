import { storeJson } from '../fileModels/store.json'
import { i18n } from '../i18n'
import { sdk } from '../sdk'
import {
  defaultBotAloneMinutes,
  defaultBotMaxMinutes,
  defaultBotName,
  hostPattern,
  normalizeHosts,
} from '../utils'

const { InputSpec, List, Value } = sdk

export const inputSpec = InputSpec.of({
  domains: Value.list(
    List.text(
      {
        name: i18n('Allowed Jitsi servers'),
        description: i18n(
          'Host names of your own Jitsi servers, for example meet.example.com. The bot only joins https links on these hosts. Leave the list empty to turn the bot off. Never add a host you do not control: the bot opens it in a browser on this server.',
        ),
        default: [],
        maxLength: 20,
      },
      {
        placeholder: 'meet.example.com',
        inputmode: 'text',
        maxLength: 253,
        patterns: [
          {
            regex: hostPattern,
            description: i18n(
              'Enter a host name such as meet.example.com, without https:// or a path.',
            ),
          },
        ],
      },
    ),
  ),
  botName: Value.text({
    name: i18n('Bot name'),
    description: i18n('The name participants see when the bot joins.'),
    required: true,
    default: defaultBotName,
    minLength: 1,
    maxLength: 60,
  }),
  announce: Value.toggle({
    name: i18n('Announce recording in the meeting chat'),
    description: i18n(
      'The bot posts a notice that the meeting is being recorded and transcribed. Recording people without telling them is unlawful in many places: leave this on unless everyone already knows.',
    ),
    default: true,
  }),
  maxMinutes: Value.number({
    name: i18n('Maximum meeting length'),
    description: i18n('The bot leaves and ends the meeting after this long.'),
    required: true,
    default: defaultBotMaxMinutes,
    min: 5,
    max: 1440,
    integer: true,
    units: i18n('minutes'),
  }),
  aloneMinutes: Value.number({
    name: i18n('Leave when alone for'),
    description: i18n(
      'The bot leaves when no other participant has been in the meeting for this long.',
    ),
    required: true,
    default: defaultBotAloneMinutes,
    min: 1,
    max: 120,
    integer: true,
    units: i18n('minutes'),
  }),
})

export const configureJitsi = sdk.Action.withInput(
  'configure-jitsi',

  async () => ({
    name: i18n('Configure Jitsi Bot'),
    description: i18n(
      'Allow the notetaker bot to join meetings on your Jitsi servers and set how it behaves. The bot stays off until you add at least one server.',
    ),
    warning: null,
    allowedStatuses: 'any',
    group: i18n('Configuration'),
    visibility: 'enabled',
  }),

  inputSpec,

  async () => {
    const j = await storeJson.read((s) => s.jitsi).once()
    return j ?? null
  },

  async ({ effects, input }) => {
    await storeJson.merge(effects, {
      jitsi: {
        domains: normalizeHosts(input.domains),
        botName: input.botName.trim(),
        announce: input.announce,
        maxMinutes: input.maxMinutes,
        aloneMinutes: input.aloneMinutes,
      },
    })
  },
)
