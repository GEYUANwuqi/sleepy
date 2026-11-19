import { test, expect } from '@playwright/test'

test('initialize admin, create device, report, live privacy change and persistence', async ({
  page,
  browser,
  request,
}) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  const defaultBackground = 'https://t.alcy.cc/fj'
  await page.route(defaultBackground, (route) => route.abort())
  await page.goto('/')
  await expect(page.getByRole('link', { name: /管理/ })).toHaveCount(0)
  await expect(page.getByText('暂无公开设备')).toBeVisible()
  await page.goto('/admin')
  await page.getByLabel('管理员密码').fill('Initial123!')
  await page.getByRole('button', { name: '登录管理面板' }).click()
  await expect(page.getByRole('heading', { name: '设置你的密码' })).toBeVisible()
  await page.getByLabel('新密码', { exact: true }).fill('NewPassword123!')
  await page.getByLabel('确认新密码').fill('NewPassword123!')
  await page.getByRole('button', { name: '修改密码并重新登录' }).click()
  await page.getByLabel('管理员密码').fill('NewPassword123!')
  await page.getByRole('button', { name: '登录管理面板' }).click()
  await page.getByRole('button', { name: '＋ 添加设备', exact: true }).click()
  await page.getByLabel('设备名称').fill('开发笔记本')
  await page.getByLabel('设备类别').selectOption('laptop')
  await page.getByLabel('公开活动描述').check()
  await page.getByLabel('公开电池电量').check()
  await expect(page.getByLabel('接收CPU 使用率')).toHaveCount(0)
  await page.getByRole('button', { name: '添加字段', exact: true }).click()
  await page.getByLabel('启用CPU 使用率').check()
  await page.getByLabel('启用内存使用率').check()
  await page.getByLabel('字段标识', { exact: true }).fill('temperature')
  await page.getByLabel('显示名称', { exact: true }).fill('温度（℃）')
  await page.getByRole('combobox', { name: '字段类型', exact: true }).selectOption('number')
  await page.getByRole('button', { name: '创建自定义字段' }).click()
  await expect(page.getByLabel('启用温度（℃）')).not.toBeChecked()
  await page.getByLabel('启用温度（℃）').check()
  await page.getByLabel('字段标识', { exact: true }).fill('note')
  await page.getByLabel('显示名称', { exact: true }).fill('备注')
  await page.getByRole('button', { name: '创建自定义字段' }).click()
  await expect(page.getByLabel('启用备注')).not.toBeChecked()
  await page.getByRole('button', { name: '完成' }).click()
  await expect(page.getByLabel('接收备注')).toHaveCount(0)
  await expect(page.locator('.field-cards > :last-child')).toHaveAccessibleName('添加字段')
  await expect(page.getByLabel('接收CPU 使用率')).not.toBeChecked()
  await expect(page.getByLabel('公开CPU 使用率')).toBeDisabled()
  await page.getByLabel('接收CPU 使用率').check()
  await page.getByLabel('公开CPU 使用率').check()
  await page.getByLabel('接收温度（℃）').check()
  await page.getByLabel('公开温度（℃）').check()
  await page.getByLabel('自定义设备提示').check()
  await page.getByLabel('在线但未使用时的提示').fill('合盖休眠')
  await page.getByLabel('离线提示', { exact: true }).fill('笔记本失联了喵')
  await page.getByRole('button', { name: '保存设备', exact: true }).click()
  const key = (await page.locator('.secret-key').textContent())!
  await page.getByRole('button', { name: '已保存，关闭' }).click()
  const response = await request.post('/api/device/report', {
    headers: { Authorization: `Bearer ${key}` },
    data: {
      using: true,
      status: 'Visual Studio Code',
      battery_percent: 86,
      charging: true,
      cpu_percent: 23.5,
      temperature: 54,
    },
  })
  expect(response.status()).toBe(204)
  const visitor = await browser.newContext()
  const publicPage = await visitor.newPage()
  // The status page must remain usable when the image service is offline.
  await publicPage.route(defaultBackground, (route) => route.abort())
  const customBackground = 'https://example.org/background.png'
  await publicPage.route(customBackground, (route) =>
    route.fulfill({
      contentType: 'image/png',
      body: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO9W8L8AAAAASUVORK5CYII=',
        'base64',
      ),
    }),
  )
  await publicPage.goto('/')
  await expect(publicPage.locator('.public-wallpaper')).toHaveAttribute('src', defaultBackground)
  await expect(publicPage.getByRole('heading', { name: '开发笔记本' })).toBeVisible()
  await expect(publicPage.getByText('Visual Studio Code')).toBeVisible()
  await expect(publicPage.getByText('温度（℃）')).toBeVisible()
  await expect(publicPage.getByText('23.5%')).toBeVisible()
  await page.getByLabel('启用SMTC').click()
  const musicKey = (await page.locator('.secret-key').textContent())!
  await page.getByRole('button', { name: '已保存，关闭' }).click()
  await expect(page.getByLabel('启用SMTC')).toBeChecked()
  await expect(page.locator('.managed-devices > :first-child')).toHaveAccessibleName('SMTC 音乐')
  await expect(publicPage.locator('.device-grid > :first-child')).toHaveAccessibleName('SMTC 音乐')
  const track = { playback: 'playing', title: '测试曲目', artist: '测试歌手', album: '测试专辑' }
  const musicHeaders = { Authorization: `Bearer ${musicKey}` }
  expect(
    (await request.post('/api/smtc/report', { headers: musicHeaders, data: track })).status(),
  ).toBe(204)
  await expect(publicPage.getByRole('heading', { name: '测试曲目' })).toBeVisible()
  await request.post('/api/smtc/report', {
    headers: musicHeaders,
    data: { ...track, playback: 'paused' },
  })
  await expect(publicPage.getByText('已暂停')).toBeVisible()
  await publicPage.screenshot({ path: 'test-results/public-desktop.png', fullPage: true })
  await publicPage.setViewportSize({ width: 390, height: 844 })
  await publicPage.screenshot({ path: 'test-results/public-mobile.png', fullPage: true })
  expect(
    await publicPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
  ).toBeTruthy()
  await page.getByLabel('启用SMTC').click()
  await expect(publicPage.getByRole('article', { name: 'SMTC 音乐' })).toHaveCount(0)
  await page.getByRole('button', { name: '编辑设备', exact: true }).click()
  await page.getByLabel('离线阈值（秒）').fill('10')
  await page.getByRole('button', { name: '保存设备', exact: true }).click()
  for (const view of [publicPage, page]) {
    await expect(view.getByText('笔记本失联了喵', { exact: true })).toBeVisible({ timeout: 15000 })
    await expect(view.getByText(/Visual Studio Code|86%|23\.5%|温度（℃）/)).toHaveCount(0)
  }
  // Editing a Vue-reactive device must open correctly, and privacy updates propagate over SSE.
  await request.post('/api/device/report', {
    headers: { Authorization: `Bearer ${key}` },
    data: { using: false },
  })
  await expect(publicPage.getByText('合盖休眠')).toBeVisible()
  await page.getByRole('button', { name: '编辑设备', exact: true }).click()
  await expect(page.getByLabel('接收内存使用率')).not.toBeChecked()
  await page.getByRole('button', { name: '停用内存使用率' }).click()
  await expect(page.getByLabel('接收内存使用率')).toHaveCount(0)
  await page.getByRole('button', { name: '添加字段', exact: true }).click()
  await expect(page.getByLabel('启用内存使用率')).not.toBeChecked()
  await expect(page.getByLabel('启用备注')).not.toBeChecked()
  await page.getByRole('button', { name: '删除备注' }).click()
  await expect(page.getByLabel('启用备注')).toHaveCount(0)
  await page.getByLabel('启用GPU 使用率').check()
  await page.getByRole('button', { name: '取消' }).click()
  await expect(page.getByLabel('接收GPU 使用率')).toHaveCount(0)
  await page.getByRole('button', { name: '添加字段', exact: true }).click()
  await expect(page.getByLabel('启用备注')).not.toBeChecked()
  await page.getByRole('button', { name: '删除备注' }).click()
  await page.getByRole('button', { name: '完成' }).click()
  await page.getByLabel('自定义设备提示').uncheck()
  await page.getByRole('button', { name: '保存设备', exact: true }).click()
  await expect(publicPage.getByText('未在使用', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '编辑设备', exact: true }).click()
  await page.getByRole('button', { name: '添加字段', exact: true }).click()
  await expect(page.getByLabel('启用备注')).toHaveCount(0)
  await page.getByRole('button', { name: '取消' }).click()
  await page.getByLabel('隐藏此设备').check()
  await page.getByRole('button', { name: '保存设备', exact: true }).click()
  await expect(publicPage.getByRole('heading', { name: '开发笔记本' })).toHaveCount(0)
  await expect(publicPage.getByText('暂无公开设备')).toBeVisible()
  expect((await visitor.request.get('/api/admin/devices')).status()).toBe(401)
  await page.screenshot({ path: 'test-results/admin-desktop.png', fullPage: true })
  await expect(page.getByText('默认设备提示', { exact: true })).toHaveCount(0)
  await page.getByRole('link', { name: '站点与安全' }).click()
  await page.getByLabel('Token 名称').fill('站点脚本')
  await page.getByRole('button', { name: '创建 API Token' }).click()
  const tokenDialog = page.getByRole('dialog', { name: '保存 API Token' })
  const tokenHeaders = {
    Authorization: 'Bearer ' + (await tokenDialog.locator('.secret-key').textContent()),
  }
  await tokenDialog.getByRole('button', { name: '已保存，关闭' }).click()
  expect(
    (
      await visitor.request.patch('/api/admin/settings', {
        headers: tokenHeaders,
        data: { display: { footer: '脚本同步' } },
      })
    ).status(),
  ).toBe(204)
  await expect(publicPage.getByText('脚本同步')).toBeVisible()
  await page.getByLabel('新状态标识').fill('reading')
  await page.getByLabel('新状态名称').fill('看书中')
  await page.getByRole('button', { name: '添加状态' }).click()
  await expect
    .poll(async () =>
      (await visitor.request.get('/api/admin/settings', { headers: tokenHeaders }))
        .json()
        .then((data) => data.settings.labels.reading?.name),
    )
    .toBe('看书中')
  expect(
    (
      await visitor.request.put('/api/admin/status', {
        headers: tokenHeaders,
        data: { status: 'reading' },
      })
    ).status(),
  ).toBe(204)
  await expect(publicPage.getByRole('heading', { name: '看书中' })).toBeVisible()
  expect(
    (
      await visitor.request.put('/api/admin/status', {
        headers: tokenHeaders,
        data: { status: null },
      })
    ).status(),
  ).toBe(204)
  await expect(publicPage.getByRole('heading', { name: '看书中' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '保存设置' })).toHaveCount(0)
  await page.getByLabel('站点标题').fill('')
  await expect(page.getByRole('alert')).toContainText('尚未保存')
  await expect(publicPage).toHaveTitle('似了吗？')
  await page.getByLabel('站点标题').fill('属于我的小小近况')
  await page.getByLabel('无设备提示').fill('设备暂未公开')
  await page.getByLabel('背景图片地址').fill(customBackground)
  await page.getByLabel('卡片不透明度').fill('78')
  await page.getByLabel('站点标题').fill('属于我的小小近况')
  await page.getByLabel('链接文字').fill('个人主页')
  await page.getByLabel('链接地址').fill('https://example.org/')
  await expect(page.getByText('已自动保存', { exact: true })).toBeVisible()
  await expect(publicPage.getByRole('heading', { name: '属于我的小小近况' })).toBeVisible()
  await expect(publicPage.getByText('设备暂未公开')).toBeVisible()
  await expect(publicPage.getByRole('link', { name: '个人主页' })).toHaveAttribute(
    'href',
    'https://example.org/',
  )
  await expect(publicPage.locator('.public-wallpaper.loaded')).toHaveAttribute(
    'src',
    customBackground,
  )
  await expect(publicPage.locator('.glass-card').first()).toHaveCSS('--card-opacity', '78%')
  await expect(publicPage).toHaveTitle('属于我的小小近况')
  await expect(publicPage.getByText('脚本同步')).toBeVisible()
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '撤销 站点脚本' }).click()
  await expect(page.getByRole('button', { name: '撤销 站点脚本' })).toHaveCount(0)
  expect(
    (await visitor.request.get('/api/admin/settings', { headers: tokenHeaders })).status(),
  ).toBe(401)
  await page.reload()
  await expect(page.getByLabel('站点标题')).toHaveValue('属于我的小小近况')
  await expect(page.getByLabel('背景图片地址')).toHaveValue(customBackground)
  await expect(page.getByLabel('无设备提示')).toHaveValue('设备暂未公开')
  await page.getByLabel('背景图片地址').fill('')
  await page.getByLabel('无设备提示').fill('')
  await expect(page.getByText('已自动保存', { exact: true })).toBeVisible()
  await expect(publicPage.locator('.public-wallpaper')).toHaveCount(0)
  await expect(publicPage.getByRole('heading', { name: '设备', exact: true })).toHaveCount(0)
  await page.getByRole('link', { name: '访问统计' }).click()
  await expect(page.getByRole('heading', { name: '请求明细' })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page.getByRole('heading', { name: '管理员登录' })).toBeVisible()
  expect(errors).toEqual([])
  await visitor.close()
})
