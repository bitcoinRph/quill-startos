import { i18n } from './i18n'
import { sdk } from './sdk'
import { uiHostId, uiPort } from './utils'

export const setInterfaces = sdk.setupInterfaces(async ({ effects }) => {
  const uiMulti = sdk.MultiHost.of(effects, uiHostId)
  const uiMultiOrigin = await uiMulti.bindPort(uiPort, {
    protocol: 'http',
  })

  const ui = sdk.createInterface(effects, {
    name: i18n('Web UI'),
    id: 'ui',
    description: i18n(
      'The Quill web app. Sign in with the admin password from Set Admin Password. Use an HTTPS address: the browser recorder needs a secure context.',
    ),
    type: 'ui',
    masked: false,
    schemeOverride: null,
    username: null,
    path: '',
    query: {},
  })

  const api = sdk.createInterface(effects, {
    name: i18n('REST API'),
    id: 'api',
    description: i18n(
      'OpenAPI document of the REST API. Call /api/v1 on this address with the header Authorization: Bearer plus the API key from Get API Credentials. Also serves the OpenAI-compatible /v1/audio/transcriptions and the Granola-compatible /v1/notes. Tools built for the Granola API work after two changes: the base URL (https://public-api.granola.ai becomes this address) and the API key.',
    ),
    type: 'api',
    masked: false,
    schemeOverride: null,
    username: null,
    path: '/openapi.json',
    query: {},
  })

  const mcp = sdk.createInterface(effects, {
    name: i18n('MCP'),
    id: 'mcp',
    description: i18n(
      'Model Context Protocol endpoint (streamable HTTP) for AI agents. Authenticate with the header Authorization: Bearer plus the API key from Get API Credentials.',
    ),
    type: 'api',
    masked: false,
    schemeOverride: null,
    username: null,
    path: '/mcp',
    query: {},
  })

  return [await uiMultiOrigin.export([ui, api, mcp])]
})
