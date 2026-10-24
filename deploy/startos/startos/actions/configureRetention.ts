import { storeJson } from '../fileModels/store.json'
import { i18n } from '../i18n'
import { sdk } from '../sdk'

const { InputSpec, Value } = sdk

export const inputSpec = InputSpec.of({
  audioDays: Value.number({
    name: i18n('Keep audio for'),
    description: i18n(
      'Delete the audio of a meeting after this many days. Transcripts and notes are kept. 0 keeps audio forever.',
    ),
    required: true,
    default: 0,
    min: 0,
    max: 3650,
    integer: true,
    units: i18n('days'),
  }),
  compressAudio: Value.toggle({
    name: i18n('Compress audio'),
    description: i18n(
      'Convert finished recordings to Opus to save disk space. Transcription is not affected.',
    ),
    default: true,
  }),
})

export const configureRetention = sdk.Action.withInput(
  'configure-retention',

  async () => ({
    name: i18n('Configure Retention'),
    description: i18n(
      'Choose how long recordings are kept and whether they are compressed.',
    ),
    warning: null,
    allowedStatuses: 'any',
    group: i18n('Configuration'),
    visibility: 'enabled',
  }),

  inputSpec,

  async () => (await storeJson.read((s) => s.retention).once()) ?? null,

  async ({ effects, input }) => {
    await storeJson.merge(effects, {
      retention: {
        audioDays: input.audioDays,
        compressAudio: input.compressAudio,
      },
    })
  },
)
