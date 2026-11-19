<script setup lang="ts">
import type { MusicState } from './api'
defineProps<{ music: MusicState }>()
const names = { playing: '播放中', paused: '已暂停', idle: '未播放', unknown: '等待上报' }
</script>
<template>
  <article class="device-card music-card" aria-label="SMTC 音乐">
    <div class="device-icon" aria-hidden="true">♫</div>
    <div class="device-details">
      <h3>{{ music.title || 'SMTC 音乐' }}</h3>
      <p v-if="music.artist || music.album" class="small muted">
        {{ [music.artist, music.album].filter(Boolean).join(' · ') }}
      </p>
    </div>
    <div class="device-readings">
      <span class="badge" :class="{ green: music.playback === 'playing' }">{{
        names[music.playback]
      }}</span>
      <div v-if="$slots.default"><slot /></div>
    </div>
  </article>
</template>
