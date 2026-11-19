import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import PublicPage from './PublicPage.vue'
import './style.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: PublicPage },
    { path: '/admin/:section?', component: () => import('./AdminPage.vue') },
  ],
})
createApp(App).use(router).mount('#app')
