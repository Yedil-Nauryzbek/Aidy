"""Observed browser operations bound to one native window and real tab IDs."""
import json
import time
import re
from urllib.parse import urlsplit

from .ai_tools import AIError, AIPlanError, AIClarification

CLOSING = {'close_tab', 'close_all_tabs', 'close_other_tabs', 'close_matching_tabs', 'close_tabs_except'}


def tab_targets(tabs, operation, text=''):
    if not tabs:
        raise AIPlanError('The browser exposed no tab strip. Inspect a browser window with accessible tabs.')
    if operation == 'close_all_tabs':
        return [row['id'] for row in tabs]
    selected = [row for row in tabs if row.get('selected')]
    if operation == 'close_other_tabs':
        if len(selected) != 1:
            raise AIClarification('Select the tab to keep, then repeat the request.')
        return [row['id'] for row in tabs if row['id'] != selected[0]['id']]
    query = text.strip().casefold()
    if query.isdecimal() and operation in {'select_tab','close_tab'}:
        index=int(query)-1
        if not 0<=index<len(tabs):raise AIClarification('That tab position does not exist.')
        return [tabs[index]['id']]
    if operation in {'close_matching_tabs','close_tabs_except'}:
        if not query:raise AIClarification('Which tab title should I match?')
        matches=[row for row in tabs if query in row['name'].casefold()]
        if not matches:raise AIClarification('No tab matches that title. Which tabs do you mean?')
        if operation=='close_tabs_except':return [row['id'] for row in tabs if row not in matches]
        return [row['id'] for row in matches]
    matches = ([row for row in tabs if row['id']==text or row['name'].casefold()==query] or
               [row for row in tabs if query in row['name'].casefold()]) if query else selected
    if not matches:
        raise AIClarification('Which browser tab do you mean?')
    if len(matches)!=1 and operation!='close_matching_tabs':
        raise AIClarification('Several tabs match. Give a more specific tab title.')
    return [row['id'] for row in matches]


class BrowserAutomation:
    def __init__(self, automation):
        self.a = automation

    def state(self, window):
        view = self.a.snapshot(window, chrome_only=True)
        tabs = [dict(row,name=re.sub(r'\s*[-–—]\s*(?:Memory usage|Использование памяти|Потребление памяти)\s*[-:—].*$',
                    '',row['name'],flags=re.I)) for row in view['elements'] if row['control']==50019]
        return view, tabs

    def run(self, window, request):
        import pyautogui
        pyautogui.PAUSE = 0
        action, text = request['action'], request.get('text','')
        self.a.check(window)
        view, tabs = self.state(window)
        if action=='tabs':
            label=f'{len(tabs)} tabs: '+', '.join(t['name'][:60] for t in tabs[:12])
            if len(tabs)>12:label+=f', and {len(tabs)-12} more.'
            return dict(status='verified',message=label,tabs=tabs,truncated=view['truncated'])
        if action=='read_page':
            docs=[self.a.elements[window['hwnd']][e['id']] for e in view['elements'] if e['control']==50030]
            if not docs:
                raise AIPlanError('No accessible page document is available in this browser window.')
            return dict(status='verified',message='\n'.join(self.a.text(e,4000) for e in docs)[:6000])
        if action in CLOSING or action=='select_tab':
            if view['truncated']:
                raise AIPlanError('The tab strip observation was incomplete. No tabs were closed; inspect a smaller browser window or group first.')
            ids=[row['id'] for row in tabs]
            if 'expected_tabs' in request and ids!=request['expected_tabs']:
                raise AIPlanError('The tabs changed after selection. No tab was closed; select and confirm again.')
            if any(row['name']!=request.get('expected_names',{}).get(row['id'],row['name']) for row in tabs):
                raise AIPlanError('A selected tab changed after confirmation. No tab was closed.')
            targets=request.get('target_tabs',tab_targets(tabs,action,text))
            if any(ref not in ids for ref in targets):
                raise AIPlanError('A selected browser tab disappeared. No tab was closed.')
            if not targets:
                return dict(status='verified',message='There are no other tabs to close.',closed_count=0)
            self.a.browser_progress=(0,len(targets))
            self.a.check(window,True)
            kept=set(ids)-set(targets)
            blank=set()
            if action!='select_tab' and set(targets)==set(ids):
                self.a.effect()
                # Open a literal blank page, avoiding browser-specific New Tab
                # onboarding/content while preserving every original page.
                pyautogui.hotkey('ctrl','l')
                field=self.a.client.GetFocusedElement()
                if field.CurrentControlType!=50004:
                    raise AIError('The address field did not receive focus. No original tab was closed.')
                self.a.set_text(window,field,'about:blank')
                self.a.check(window,True)
                pyautogui.hotkey('alt','enter')
                end=time.monotonic()+1.2
                while time.monotonic()<end:
                    self.a.check(window,True)
                    _, current=self.state(window)
                    blank={r['id'] for r in current}-set(ids)
                    if len(blank)==1:break
                    time.sleep(.02)
                if len(blank)!=1:
                    raise AIError('The blank replacement tab was not verified. No original tab was closed.')
            closed=[]
            for ref in targets:
                self.a.check(window,True)
                _, current=self.state(window)
                live={r['id'] for r in current}
                if live!=(set(ids)-set(closed))|blank:
                    raise AIError('The browser tabs changed unexpectedly. Remaining closures stopped.')
                element=self.a.elements[window['hwnd']].get(ref)
                if element is None:
                    raise AIError('The selected tab is no longer available. Remaining closures stopped.')
                self.a.invoke(element,'select')
                selected=self.a.pattern(element,10010,'IUIAutomationSelectionItemPattern')
                if not selected or not selected.CurrentIsSelected:
                    raise AIError('The selected tab did not become active. No close key was sent.')
                self.a.check(window,True)
                if action=='select_tab':
                    return dict(status='verified',message='Tab selected.',tabs=current)
                self.a.effect()
                pyautogui.hotkey('ctrl','w')
                end=time.monotonic()+1.0
                while time.monotonic()<end:
                    self.a.check(window,True)
                    _, current=self.state(window)
                    if ref not in {r['id'] for r in current}:break
                    time.sleep(.025)
                else:
                    raise AIError('Tab closure was not verified. It will not be repeated; check for an unsaved-page dialog.')
                closed.append(ref)
                self.a.browser_progress=(len(closed),len(targets))
            _, after=self.state(window)
            live={r['id'] for r in after}
            if not (kept|blank)<=live or set(targets)&live:
                raise AIError('The browser result could not be verified. Remaining actions stopped.')
            return dict(status='verified',message=f'Closed {len(closed)} '+('tab.' if len(closed)==1 else 'tabs.'),closed_count=len(closed),tabs=after)
        self.a.check(window,True)
        if action=='navigate':
            url=urlsplit(text)
            if (url.scheme not in {'http','https'} or not url.netloc or url.username or url.password
                    or len(text)>2000 or any(ord(c)<33 for c in text)):
                raise AIPlanError('Browser navigation needs a normal HTTP or HTTPS address.')
            self.a.effect()
            pyautogui.hotkey('ctrl','l')
            field=self.a.client.GetFocusedElement()
            if field.CurrentControlType!=50004:
                raise AIError('The browser address field did not receive focus. No address was entered.')
            self.a.set_text(window,field,text)
            self.a.check(window,True)
            pyautogui.press('enter')
            return dict(status='dispatched',message='Address submitted; page loading is not yet verified.')
        if action=='find':
            # The existing owned-popup-aware Find implementation is retained.
            return None
        chords={'new_tab':('ctrl','t'),'next_tab':('ctrl','tab'),'previous_tab':('ctrl','shift','tab'),
                'back':('alt','left'),'forward':('alt','right'),'reload':('ctrl','r'),
                'restore_tab':('ctrl','shift','t')}
        if action not in chords:
            raise AIPlanError('Choose an available browser operation.')
        self.a.effect()
        pyautogui.hotkey(*chords[action])
        end=time.monotonic()+.8
        while time.monotonic()<end:
            self.a.check(window,True)
            after, current=self.state(window)
            if after['view']!=view['view']:
                return dict(status='verified',message='Browser state changed.',tabs=current)
            time.sleep(.03)
        return dict(status='dispatched',message='Browser navigation sent; the final page state is not yet verified.')
