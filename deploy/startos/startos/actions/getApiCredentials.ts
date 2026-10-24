import { storeJson } from '../fileModels/store.json'
import { i18n } from '../i18n'
import { sdk } from '../sdk'
import { pickUrl, uiHostId, uiPort } from '../utils'

const placeholderOrigin = 'https://quill.local'

export const getApiCredentials = sdk.Action.withoutInput(
  'get-api-credentials',

  async () => ({
    name: i18n('Get API Credentials'),
    description: i18n(
      'Show the service API key, the MCP URL and ready-made commands to connect Claude Code or any MCP client.',
    ),
    warning: null,
    allowedStatuses: 'any',
    group: null,
    visibility: 'enabled',
  }),

  async ({ effects }) => {
    const apiKey = await storeJson.read((s) => s.apiKey).once()
    if (!apiKey) throw new Error(i18n('No API key has been generated yet'))

    const urls = (await sdk.host
      .getOwn(effects, uiHostId, (host) => {
        const address = host?.bindings[uiPort]?.interfaces['mcp']?.addressInfo
        if (!address) return { preferred: [], all: [] }
        return {
          preferred: address.public.format('urlstring'),
          all: address.nonLocal.format('urlstring'),
        }
      })
      .once()) ?? { preferred: [], all: [] }

    const picked = pickUrl(urls.preferred, urls.all)
    const origin = picked ? new URL(picked).origin : placeholderOrigin
    const mcpUrl = `${origin}/mcp`
    const restUrl = `${origin}/api/v1`

    const claudeCode = `claude mcp add --transport http quill ${mcpUrl} --header "Authorization: Bearer ${apiKey}"`
    const mcpJson = JSON.stringify({
      mcpServers: {
        quill: {
          type: 'http',
          url: mcpUrl,
          headers: { Authorization: `Bearer ${apiKey}` },
        },
      },
    })

    return {
      version: '1',
      title: i18n('API Credentials'),
      message: picked
        ? i18n(
            'Send the API key as the header Authorization: Bearer <key> to the REST API and the MCP endpoint.',
          )
        : i18n(
            'No address is enabled for Quill yet, so the URLs below use a placeholder. Enable an address on the Interfaces page and run this action again.',
          ),
      result: {
        type: 'group',
        value: [
          {
            type: 'single',
            name: i18n('API Key'),
            description: i18n(
              'Bearer credential for the REST API and the MCP endpoint',
            ),
            value: apiKey,
            masked: true,
            copyable: true,
            qr: false,
          },
          {
            type: 'single',
            name: i18n('MCP URL'),
            description: i18n('Streamable HTTP MCP endpoint'),
            value: mcpUrl,
            masked: false,
            copyable: true,
            qr: false,
          },
          {
            type: 'single',
            name: i18n('REST API URL'),
            description: i18n('Base URL of the REST API'),
            value: restUrl,
            masked: false,
            copyable: true,
            qr: false,
          },
          {
            type: 'single',
            name: i18n('Claude Code command'),
            description: i18n(
              'Run this in a terminal to add Quill to Claude Code',
            ),
            value: claudeCode,
            masked: true,
            copyable: true,
            qr: false,
          },
          {
            type: 'single',
            name: i18n('MCP JSON configuration'),
            description: i18n(
              'Paste into the MCP settings of any client that reads mcpServers JSON',
            ),
            value: mcpJson,
            masked: true,
            copyable: true,
            qr: false,
          },
        ],
      },
    }
  },
)
