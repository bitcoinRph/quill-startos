import { setupManifest } from '@start9labs/start-sdk'
import { long, openwhisprDependency, ollamaDependency, short } from './i18n'

export const manifest = setupManifest({
  id: 'quill',
  title: 'Quill',
  license: 'MIT',
  packageRepo: 'https://github.com/bitcoinRph/quill-startos',
  upstreamRepo: 'https://github.com/humanitas-labs/quill',
  marketingUrl: 'https://github.com/humanitas-labs/quill',
  donationUrl: null,
  description: { short, long },
  volumes: ['main', 'models'],
  images: {
    quill: {
      source: {
        dockerBuild: {
          workdir: '../../server',
          dockerfile: '../../server/Dockerfile',
        },
      },
      arch: ['x86_64', 'aarch64'],
    },
  },
  dependencies: {
    openwhispr: {
      description: openwhisprDependency,
      optional: true,
      s9pk: null,
    },
    ollama: {
      description: ollamaDependency,
      optional: true,
      s9pk: null,
    },
  },
})
