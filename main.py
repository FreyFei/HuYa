#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虎牙虎粮自动发放 - v4 加固版
环境变量配置，核心逻辑极简

v4 改动（针对第 9/10 次失败）：
- 重启保护扩到每一步：查虎粮、每个房间赠送，撞上渲染器无响应就重启
  整个浏览器、重新登录后重试一次（v3 只保护了登录）。
- 背包读到 0 时二次确认：0 可能是背包页没点开导致的误读，再读一次。
- 不按开播状态跳过房间：离线房间也可以收虎粮，两房始终平分。
"""

import os
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

import config as cfg


class _RendererDead(Exception):
    """渲染器无响应（Timed out receiving message from renderer），当前浏览器已不可用"""


class HuYaAuto:
    """精简稳定版虎牙虎粮自动发放"""

    def __init__(self):

        self.debug = ""

        if self.debug :
            print("从文件获取 HUYA_COOKIE")
            try:
                with open("cookie", "r", encoding="utf-8") as f:
                    self.cookie = f.read().strip()
            except FileNotFoundError:
                self.cookie = ""  # 文件不存在时为空
            self.rooms = ["998"]
        else :
            print("从环境变量获取 HUYA_COOKIE")
            self.cookie = os.getenv('HUYA_COOKIE', '').strip()
            self.rooms = self._parse_rooms(os.getenv('HUYA_ROOMS', ''))

        if not self.cookie:
            print("[ERROR] 未设置 HUYA_COOKIE")
            sys.exit(1)

        if not self.rooms:
            print("[WARN] 未设置房间号，使用默认房间")
            self.rooms = [518512, 518511]

        self.driver = self._init_browser()
        self.wait = WebDriverWait(self.driver, 5)

    def _parse_rooms(self, rooms_str):
        rooms = []
        for s in rooms_str.split(','):
            s = s.strip()
            if s:
                try:
                    rooms.append(int(s))
                except ValueError:
                    print(f"[WARN] 跳过无效房间号: {s}")
        return rooms

    def _init_browser(self):
        chrome_options = Options()

        if not self.debug:
            chrome_options.add_argument('--headless=new')

        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')

        chrome_options.add_argument('--disable-images')
        chrome_options.add_argument('--disable-javascript=false')

        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('--window-size=1920,1080')
        # 直播间页面资源重、默认 normal 策略会死等整页加载完导致 WebDriver 读超时；eager 只等 DOM 就绪
        chrome_options.page_load_strategy = 'eager'

        print("[START] 启动浏览器")
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=chrome_options
        )
        driver.set_page_load_timeout(30)
        driver.set_script_timeout(30)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return driver

    def _safe_get(self, url):
        """带超时兜底的页面打开：加载超时就停止加载并继续后续流程"""
        try:
            self.driver.get(url)
        except TimeoutException:
            print(f"[WARN] 页面加载超时，停止加载继续: {url}")
            try:
                self.driver.execute_script("window.stop()")
            except Exception:
                pass

    @staticmethod
    def _is_renderer_dead(e):
        return "renderer" in str(e)

    def _restart_browser(self):
        try:
            self.driver.quit()
        except Exception:
            pass
        self.driver = self._init_browser()
        self.wait = WebDriverWait(self.driver, 5)

    def _with_restart(self, fn, *args, **kwargs):
        """执行一个阶段；撞上渲染器无响应就重启浏览器、重新登录后重试一次。"""
        try:
            return fn(*args, **kwargs)
        except _RendererDead:
            print("[WARN] 阶段撞上渲染器无响应，重启浏览器后重试")
            self._restart_browser()
            if not self.login():
                print("[ERROR] 重启后重新登录失败")
                raise RuntimeError("re-login failed")
            return fn(*args, **kwargs)

    def login(self):
        # v3（11-07 第 5 次）：cookie 加完后未保护的 refresh 又撞渲染器超时、直接 CRASH。
        # 渲染器卡死后在同一浏览器上重试无用：撞上就重启整个浏览器重登，最多 3 次。
        for restart in range(3):
            try:
                return self._login_once()
            except _RendererDead:
                print(f"[WARN] 渲染器无响应，重启浏览器重登（第 {restart + 1} 次）")
                if restart == 2:
                    print("[ERROR] 多次重启浏览器仍无法登录")
                    return False
                self._restart_browser()
        return False

    def _login_once(self):
        print("[LOGIN] 登录中")
        # 11-07 定时跑死在这里：driver.get 打开个人中心页时渲染器 28.5 秒无响应
        # （Timed out receiving message from renderer），cookie 还没加就崩了。
        # login 原先没走 _safe_get；改成带重试的 _safe_get，最多 3 次。
        loaded = False
        for attempt in range(3):
            try:
                self._safe_get(cfg.URLS["user_index"])
            except Exception as e:
                print(f"[WARN] 登录页打开异常: {e}")
            try:
                if self.driver.execute_script("return document.readyState") in ("interactive", "complete"):
                    loaded = True
                    break
            except Exception as e:
                if "renderer" in str(e):
                    raise _RendererDead(str(e))
            print(f"[WARN] 登录页未就绪，第 {attempt + 1} 次重试")
            time.sleep(2)
        if not loaded:
            print("[ERROR] 登录页多次加载失败")
            return False
        time.sleep(cfg.TIMING["implicit_wait"])

        cnt = 0
        for line in self.cookie.split(';'):
            line = line.strip()
            if '=' not in line:
                continue
            name, val = line.split('=', 1)
            try:
                self.driver.add_cookie({
                    'name': name.strip(),
                    'value': val.strip(),
                    'domain': '.huya.com',
                    'path': '/'
                })
                cnt += 1
            except Exception:
                continue

        print(f"[COOKIE] 已添加 {cnt} 个Cookie")
        try:
            self.driver.refresh()
        except Exception as e:
            if "renderer" in str(e):
                raise _RendererDead(str(e))
            print("[WARN] 刷新超时，停止加载继续")
            try:
                self.driver.execute_script("window.stop()")
            except Exception:
                pass
        time.sleep(cfg.TIMING["page_load_wait"])

        try:
            elem = self.wait.until(
                EC.presence_of_element_located((By.ID, cfg.LOGIN["huya_num"]))
            )
            username = elem.text.strip()
            print(f"[SUCCESS] 登录成功: {username}")
            return True
        except Exception as e:
            if "renderer" in str(e):
                raise _RendererDead(str(e))
            print("[ERROR] 登录失败")
            return False

    def get_hl_count(self):
        # v4.1：第 11 次两次都因背包页打不开读到 0 就退了。
        # 区分三种状态：>=0 是读到了（0 还要二次确认），-1 是页没打开、不能下结论。
        print("[SEARCH] 查询虎粮数量")
        confirmed_zero = False
        for attempt in range(3):
            hl = self._count_once()
            if hl >= 0:
                if hl == 0 and not confirmed_zero:
                    print("[WARN] 首次读到 0，再确认一次")
                    confirmed_zero = True
                    time.sleep(2)
                    continue
                return hl
            print(f"[WARN] 背包页加载失败，第 {attempt + 1} 次重试")
            time.sleep(3)
        print("[ERROR] 背包页多次打不开，无法确认虎粮数量")
        return -1

    def _count_once(self):
        # v4：第 10 次死在这里：裸 driver.get 打开背包页时渲染器超时、整个程序崩了。
        # 改成 _safe_get，撞上渲染器无响应就抛给外层重启浏览器重试。
        self._safe_get(cfg.URLS["pay_index"])
        time.sleep(3)

        try:
            # v4.1：第 11 次背包页加载超时、15 秒等不到标签就判了 0；放宽到 30 秒。
            pack_tab = WebDriverWait(self.driver, 30).until(
                EC.element_to_be_clickable((By.ID, cfg.PAY_PAGE["pack_tab"]))
            )
            pack_tab.click()
            time.sleep(1.5)
        except Exception as e:
            if self._is_renderer_dead(e):
                raise _RendererDead(str(e))
            print("[WARN] 背包标签等不到（页可能没加载完）")
            return -1

        try:
            # v4.1：三态返回值——数字串是读到了，'0' 是面板已加载但确实没虎粮，
            # null 是面板根本没加载出礼物项（不能判 0）。
            n = self.driver.execute_script('''
                let maxWait = 20;
                function findHuliang() {
                    const items = document.querySelectorAll('li[data-num]');
                    if (items.length === 0) return null;
                    for (let item of items) {
                        let title = item.title || item.innerText || '';
                        if (title.includes('虎粮')) {
                            return item.getAttribute('data-num');
                        }
                    }
                    return '0';
                }
                while(maxWait-- > 0) {
                    let res = findHuliang();
                    if(res !== null) return res;
                    await new Promise(r => setTimeout(r, 200));
                }
                return null;
            ''')
        except Exception as e:
            if self._is_renderer_dead(e):
                raise _RendererDead(str(e))
            print("[WARN] 读取虎粮数量失败")
            return -1

        if n is None:
            print("[WARN] 背包面板未加载出礼物项")
            return -1
        hl = int(n) if n and str(n).isdigit() else 0
        print(f"[COUNT] 虎粮数量: {hl}")
        return hl

    def send_to_room(self, room_id, count):
        print(f"[GIFT] 房间 {room_id} 发送 {count} 个")
        if count <= 0:
            return 0

        try:
            return self._send_once(room_id, count)
        except _RendererDead:
            # v4：单个房间赠送时撞死不连累下一个房间，重启浏览器、重新登录后只重试这一个房间。
            print(f"[WARN] 房间 {room_id} 赠送时渲染器无响应，重启浏览器重试一次")
            self._restart_browser()
            if not self.login():
                print(f"[ERROR] 重启后重新登录失败，房间 {room_id} 跳过")
                return 0
            try:
                return self._send_once(room_id, count)
            except _RendererDead:
                print(f"[WARN] 房间 {room_id} 重试仍撞渲染器，跳过")
                return 0

    def _send_once(self, room_id, count):
        try:
            self._safe_get(cfg.URLS["room_base"].format(room_id))
            time.sleep(cfg.TIMING["room_enter_wait"])

            lp = self.driver.execute_script('return document.body.getAttribute("data-lp")')
            gid = self.driver.execute_script('return document.body.getAttribute("data-gid")')

            if not lp or not gid:
                print("[ERROR] 获取房间参数失败")
                return 0

            self._safe_get(cfg.URLS["gift_tab"].format(lp=lp, gid=gid))
            time.sleep(cfg.TIMING["page_load_wait"])

            # 查找虎粮项
            items = self.wait.until(
                EC.presence_of_all_elements_located((By.CLASS_NAME, cfg.GIFT["item_class"]))
            )
            hu_liang = None
            for item in items:
                if "虎粮" in item.text:
                    hu_liang = item
                    break
            if not hu_liang:
                print("[ERROR] 未找到虎粮")
                return 0

            # 悬停
            ActionChains(self.driver)\
                .move_to_element(hu_liang)\
                .pause(1)\
                .perform()
            time.sleep(1)

            # 自定义数量
            inp = self.wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, cfg.GIFT["input_css"]))
            )
            inp.click()
            inp.clear()
            inp.send_keys(str(count))

            # 赠送
            send_btn = self.wait.until(
                EC.element_to_be_clickable((By.CLASS_NAME, cfg.GIFT["send_class"]))
            )
            send_btn.click()
            time.sleep(1)

            confirm_btn = self.wait.until(
                EC.element_to_be_clickable((By.CLASS_NAME, cfg.GIFT["confirm_class"]))
            )
            confirm_btn.click()
            time.sleep(1)

            print(f"[SUCCESS] 赠送成功: {count} 个")
            return count

        except Exception as e:
            if self._is_renderer_dead(e):
                raise _RendererDead(str(e))
            print(f"[CRASH] 赠送失败: {e}")
            return 0

    def run(self):
        success = False
        try:
            print("=" * 40)
            print("[HUYA] 虎牙虎粮自动发放")
            print("=" * 40)
            print(f"房间列表: {self.rooms}")

            if not self.login():
                return False

            total = self._with_restart(self.get_hl_count)
            if total < 0:
                print("❌ 背包页打不开，无法确认虎粮数量")
                return False
            if total <= 0:
                print("❌ 暂无虎粮（已二次确认）")
                return False

            print(f"[TOTAL] 虎粮总数: {total}")

            # 分配：两房平分。没开播的房间也可以收虎粮，不按开播状态跳过或重分。
            n = len(self.rooms)
            per = total // n
            rem = total % n
            plan = []
            for i, rid in enumerate(self.rooms):
                c = per + 1 if i < rem else per
                plan.append((rid, c))

            print("\n[PLAN] 分配方案:")
            for rid, c in plan:
                print(f"  {rid}: {c}个")

            print("\n[SEND] 开始发送...")
            sent = 0
            for rid, c in plan:
                sent += self.send_to_room(rid, c)

            print(f"\n[DONE] 完成！已发送 {sent}/{total}")
            success = sent > 0
            return success

        except Exception as e:
            print(f"\n[CRASH] 程序异常: {e}")
            return False

        finally:
            # 必关浏览器，修复进程泄漏
            if hasattr(self, 'driver'):
                try:
                    self.driver.quit()
                except Exception:
                    pass
                print("[EXIT] 浏览器已关闭")

            return success


def main():
    huya = HuYaAuto()
    res = huya.run()
    sys.exit(0 if res else 1)


if __name__ == '__main__':
    main()
