import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'

export const current = VersionInfo.of({
  version: '0.1.4:0',
  releaseNotes: {
    en_US:
      'Adds exact Jitsi media address mappings so a co-located notetaker can reach the local video bridge when the public address does not support NAT loopback.',
    es_ES:
      'Añade mapeos exactos de direcciones multimedia de Jitsi para que un bot de notas en el mismo servidor alcance el puente de vídeo local cuando la dirección pública no admite bucle invertido NAT.',
    de_DE:
      'Fügt exakte Jitsi-Medienadresszuordnungen hinzu, damit ein Notizen-Bot auf demselben Server die lokale Video-Bridge erreicht, wenn die öffentliche Adresse kein NAT-Loopback unterstützt.',
    pl_PL:
      'Dodaje dokładne mapowania adresów multimediów Jitsi, aby bot do notatek na tym samym serwerze mógł dotrzeć do lokalnego mostu wideo, gdy adres publiczny nie obsługuje NAT loopback.',
    fr_FR:
      'Ajoute des mappages exacts d’adresses média Jitsi afin qu’un bot de notes hébergé sur le même serveur puisse atteindre le pont vidéo local lorsque l’adresse publique ne prend pas en charge le bouclage NAT.',
  },
  migrations: {
    up: async ({ effects }) => {},
    down: IMPOSSIBLE,
  },
})
