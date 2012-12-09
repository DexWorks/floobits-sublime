# coding: utf-8
import re
import Queue
import threading
import socket
import os
import select
import json
import collections
import os.path
import hashlib
import time
import traceback
from urlparse import urlparse
from datetime import datetime

import sublime
import sublime_plugin
import dmp_monkey
dmp_monkey.monkey_patch()
from lib import diff_match_patch as dmp

__VERSION__ = '0.01'
MAX_RETRIES = 20

settings = sublime.load_settings('Floobits.sublime-settings')

COLAB_DIR = ''
PROJECT_PATH = ''
DEFAULT_HOST = ''
DEFAULT_PORT = ''
USERNAME = ''
SECRET = ''
CHAT_VIEW = None


class edit:
    def __init__(self, view):
        self.view = view

    def __enter__(self):
        self.edit = self.view.begin_edit()
        return self.edit

    def __exit__(self, type, value, traceback):
        self.view.end_edit(self.edit)


def reload_settings():
    global COLAB_DIR, DEFAULT_HOST, DEFAULT_PORT, USERNAME, SECRET
    COLAB_DIR = settings.get('share_dir', '~/.floobits/share/')
    COLAB_DIR = os.path.realpath(COLAB_DIR)
    DEFAULT_HOST = settings.get('host', 'floobits.com')
    DEFAULT_PORT = settings.get('port', 3148)
    USERNAME = settings.get('username')
    SECRET = settings.get('secret')


settings.add_on_change('', reload_settings)
reload_settings()


SOCKET_Q = Queue.Queue()
BUF_STATE = collections.defaultdict(str)
MODIFIED_EVENTS = Queue.Queue()
BUF_IDS_TO_VIEWS = {}
READ_ONLY = False


def get_full_path(p):
    full_path = os.path.join(PROJECT_PATH, p)
    return unfuck_path(full_path)


def unfuck_path(p):
    return os.path.normcase(os.path.normpath(p))


def to_rel_path(p):
    return p[len(PROJECT_PATH):]


def get_or_create_view(buf_id, path):
    view = BUF_IDS_TO_VIEWS.get(buf_id)
    if not view:
        # maybe we should create a new window? I don't know
        window = sublime.active_window()
        view = window.open_file(path)
        BUF_IDS_TO_VIEWS[buf_id] = view
        print('Created view', view)
    return view


def get_or_create_chat():
    global CHAT_VIEW
    p = get_full_path('floobits.log')
    if not CHAT_VIEW:
        window = sublime.active_window()
        CHAT_VIEW = window.open_file(p)
        CHAT_VIEW.set_read_only(True)
    return CHAT_VIEW


def get_text(view):
    return view.substr(sublime.Region(0, view.size()))


def vbid_to_buf_id(vb_id):
    for buf_id, view in BUF_IDS_TO_VIEWS.iteritems():
        if view.buffer_id() == vb_id:
            return buf_id
    return None


class DMPTransport(object):

    def __init__(self, view):
        self.buf_id = None
        self.vb_id = view.buffer_id()
        # to rel path
        self.path = to_rel_path(view.file_name())
        self.current = get_text(view)
        self.previous = BUF_STATE[self.vb_id]
        self.md5_before = hashlib.md5(self.previous).hexdigest()
        self.buf_id = vbid_to_buf_id(self.vb_id)

    def __str__(self):
        return '%s - %s - %s' % (self.buf_id, self.path, self.vb_id)

    def patches(self):
        return dmp.diff_match_patch().patch_make(self.previous, self.current)

    def to_json(self):
        patches = self.patches()
        if len(patches) == 0:
            return None
        print('sending %s patches' % len(patches))
        patch_str = ''
        for patch in patches:
            patch_str += str(patch)
        print('patch:', patch_str)
        return json.dumps({
            'id': str(self.buf_id),
            'md5_after': hashlib.md5(self.current).hexdigest(),
            'md5_before': self.md5_before,
            'path': self.path,
            'patch': patch_str,
            'name': 'patch'
        })


class AgentConnection(object):
    ''' Simple chat server using select '''

    def __init__(self, username, secret, owner, room, host=None, port=None, on_connect=None):
        global PROJECT_PATH
        self.sock = None
        self.buf = ''
        self.reconnect_delay = 500
        self.username = username
        self.secret = secret
        self.authed = False
        self.host = host or DEFAULT_HOST
        self.port = port or DEFAULT_PORT
        self.owner = owner
        self.room = room
        self.retries = MAX_RETRIES
        self.on_connect = on_connect
        # owner and room name are slugfields so this should be safe
        self.project_path = os.path.realpath(os.path.join(COLAB_DIR, self.owner, self.room))
        PROJECT_PATH = self.project_path

    def stop(self):
        self.sock.shutdown(2)
        self.sock.close()

    def send_msg(self, msg):
        self.put(json.dumps({'name': 'msg', 'data': msg}))
        self.chat(self.username, time.time(), msg)

    @staticmethod
    def put(item):
        #TODO: move json_dumps here
        if not item:
            return
        SOCKET_Q.put(item + '\n')
        qsize = SOCKET_Q.qsize()
        if qsize > 0:
            print('%s items in q' % qsize)

    def reconnect(self):
        self.sock = None
        self.authed = False
        self.reconnect_delay *= 1.5
        if self.reconnect_delay > 10000:
            self.reconnect_delay = 10000
        if self.retries > 0:
            print('reconnecting in', self.reconnect_delay)
            sublime.set_timeout(self.connect, int(self.reconnect_delay))
        else:
            print('too many reconnect failures. giving up')

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        print('Connecting to %s:%s' % (self.host, self.port))
        try:
            self.sock.connect((self.host, self.port))
        except socket.error as e:
            print('Error connecting:', e)
            self.reconnect()
            return
        self.sock.setblocking(0)
        print('connected, calling select')
        self.reconnect_delay = 1
        sublime.set_timeout(self.select, 0)
        self.auth()

    def auth(self):
        global SOCKET_Q
        # TODO: we shouldn't throw away all of this
        SOCKET_Q = Queue.Queue()
        self.put(json.dumps({
            'username': self.username,
            'secret': self.secret,
            'room': self.room,
            'room_owner': self.owner,
            'version': __VERSION__
        }))

    def get_patches(self):
        while True:
            try:
                yield SOCKET_Q.get_nowait()
            except Queue.Empty:
                break

    def chat(self, username, timestamp, msg):
        view = get_or_create_chat()
        envelope = "[{time}] <{user}> {msg}\n".format(user=username, time=timestamp, msg=msg)
        with edit(view) as ed:
            view.set_read_only(False)
            view.insert(ed, view.size(), envelope)
            view.set_read_only(True)

    def protocol(self, req):
        global READ_ONLY
        self.buf += req
        while True:
            before, sep, after = self.buf.partition('\n')
            if not sep:
                break
            data = json.loads(before)
            print(data)
            name = data.get('name')
            if name == 'patch':
                # TODO: we should do this in a separate thread
                Listener.apply_patch(data)
            elif name == 'get_buf':
                Listener.update_buf(data['id'], data['path'], data['buf'], data['md5'], save=True)
            elif name == 'room_info':
                if self.on_connect:
                    self.on_connect(self)
                    self.on_connect = None
                # Success! Reset counter
                self.retries = MAX_RETRIES
                perms = data['perms']
                if 'patch' not in perms:
                    print("We don't have patch permission. Setting buffers to read-only")
                    READ_ONLY = True
                project_json = {
                    'folders': [
                        {'path': self.project_path}
                    ]
                }
                try:
                    os.makedirs(self.project_path)
                except Exception:
                    pass
                project_fd = open(os.path.join(self.project_path, '.sublime-project'), 'w')
                project_fd.write(json.dumps(project_json, indent=4, sort_keys=True))
                project_fd.close()
                # TODO: this is hard. ST2 has no project api
#                sublime.active_window().run_command('open', {'file': self.project_path})

                for buf_id, buf in data['bufs'].iteritems():
                    Listener.update_buf(buf_id, buf['path'], "", buf['md5'])
                    # Total hack. apparently we can't create views and set their text in the same "tick"
                    Listener.get_buf(buf_id)

                self.authed = True
            elif name == 'join':
                print('%s joined the room' % data['username'])
            elif name == 'part':
                print('%s left the room' % data['username'])
                region_key = 'floobits-highlight-%s' % (data['user_id'])
                for window in sublime.windows():
                    for view in window.views():
                        view.erase_regions(region_key)
            elif name == 'highlight':
                region_key = 'floobits-highlight-%s' % (data['user_id'])
                Listener.highlight(data['id'], region_key, data['username'], data['ranges'])
            elif name == 'error':
                sublime.error_message('Floobits: Error! Message: %s' % str(data.get('msg')))
            elif name == 'disconnect':
                sublime.error_message('Floobits: Disconnected! Reason: %s' % str(data.get('reason')))
                self.retries = 0
            elif name == 'msg':
                username = data['username']
                timestamp = time.ctime(data['time'])
                msg = data.get('data')
                self.chat(username, timestamp, msg)
            else:
                print('unknown name!', name, 'data:', data)
            self.buf = after

    def select(self):
        if not self.sock:
            print('no sock')
            self.reconnect()
            return

        if not settings.get('run', True):
            return sublime.set_timeout(self.select, 1000)
        # this blocks until the socket is readable or writeable
        _in, _out, _except = select.select([self.sock], [self.sock], [self.sock])

        if _except:
            print('socket error')
            self.sock.close()
            self.reconnect()
            return

        if _in:
            buf = ''
            while True:
                try:
                    d = self.sock.recv(4096)
                    if not d:
                        break
                    buf += d
                except socket.error:
                    break
            if not buf:
                print('buf is empty')
                return self.reconnect()
            self.protocol(buf)

        if _out:
            for p in self.get_patches():
                if p is None:
                    SOCKET_Q.task_done()
                    continue
                print('writing patch: %s' % p)
                self.sock.sendall(p)
                SOCKET_Q.task_done()

        sublime.set_timeout(self.select, 100)


class Listener(sublime_plugin.EventListener):
    views_changed = []
    selection_changed = []

    @staticmethod
    def push():
        reported = set()
        while Listener.views_changed:
            view = Listener.views_changed.pop()

            vb_id = view.buffer_id()
            if vb_id in reported:
                continue

            reported.add(vb_id)
            patch = DMPTransport(view)
            # update the current copy of the buffer
            BUF_STATE[vb_id] = patch.current
            if agent:
                agent.put(patch.to_json())
            else:
                print('Not connected. Discarding view change.')

        while Listener.selection_changed:
            view = Listener.selection_changed.pop()
            if view.is_scratch():
                continue
            vb_id = view.buffer_id()
            if vb_id in reported:
                continue

            reported.add(vb_id)
            sel = view.sel()
            buf_id = vbid_to_buf_id(vb_id)
            if buf_id is None:
                print('buf_id for view not found. Not sending highlight.')
                continue
            highlight_json = json.dumps({
                'id': buf_id,
                'name': 'highlight',
                'ranges': [[x.a, x.b] for x in sel]
            })
            if agent:
                agent.put(highlight_json)
            else:
                print('Not connected. Discarding selection change.')

        sublime.set_timeout(Listener.push, 100)

    @staticmethod
    def apply_patch(patch_data):
        global MODIFIED_EVENTS
        buf_id = patch_data['id']
        path = get_full_path(patch_data['path'])
        view = get_or_create_view(buf_id, path)

        DMP = dmp.diff_match_patch()
        if len(patch_data['patch']) == 0:
            print('no patches to apply')
            return
        print('patch is', patch_data['patch'])
        dmp_patches = DMP.patch_fromText(patch_data['patch'])
        # TODO: run this in a separate thread
        old_text = get_text(view)
        md5_before = hashlib.md5(old_text).hexdigest()
        if md5_before != patch_data['md5_before']:
            print("starting md5s don't match. this is dangerous!")

        t = DMP.patch_apply(dmp_patches, old_text)

        clean_patch = True
        for applied_patch in t[1]:
            if not applied_patch:
                clean_patch = False
                break

        if not clean_patch:
            print('failed to patch')
            return Listener.get_buf(buf_id)

        cur_hash = hashlib.md5(t[0]).hexdigest()
        if cur_hash != patch_data['md5_after']:
            print('new hash %s != expected %s' % (cur_hash, patch_data['md5_after']))
            # TODO: do something better than erasing local changes
            return Listener.get_buf(buf_id)

        selections = [x for x in view.sel()]  # deep copy
        # so we don't send a patch back to the server for this
        BUF_STATE[view.buffer_id()] = str(t[0]).decode('utf-8')
        regions = []
        for patch in t[2]:
            offset = patch[0]
            length = patch[1]
            patch_text = patch[2]
            region = sublime.Region(offset, offset + length)
            regions.append(region)
            print(region)
            print('replacing', view.substr(region), 'with', patch_text.decode('utf-8'))
            MODIFIED_EVENTS.put(1)
            try:
                edit = view.begin_edit()
                view.replace(edit, region, patch_text.decode('utf-8'))
            finally:
                view.end_edit(edit)
        view.sel().clear()
        region_key = 'floobits-patch-' + patch_data['username']
        view.add_regions(region_key, regions, 'floobits.patch', 'circle', sublime.DRAW_OUTLINED)
        sublime.set_timeout(lambda: view.erase_regions(region_key), 1000)
        for sel in selections:
            print('re-adding selection', sel)
            view.sel().add(sel)

        now = datetime.now()
        view.set_status('Floobits', 'Changed by %s at %s' % (patch_data['username'], now.strftime('%H:%M')))

    @staticmethod
    def get_buf(buf_id):
        req = {
            'name': 'get_buf',
            'id': buf_id
        }
        agent.put(json.dumps(req))

    @staticmethod
    def update_buf(buf_id, path, text, md5, view=None, save=False):
        path = get_full_path(path)
        view = get_or_create_view(buf_id, path)
        visible_region = view.visible_region()
        viewport_position = view.viewport_position()
        region = sublime.Region(0, view.size())
        selections = [x for x in view.sel()]  # deep copy
        MODIFIED_EVENTS.put(1)
        # so we don't send a patch back to the server for this
        BUF_STATE[view.buffer_id()] = text.decode('utf-8')
        try:
            edit = view.begin_edit()
            view.replace(edit, region, text.decode('utf-8'))
        except Exception as e:
            print('Exception updating view:', e)
        finally:
            view.end_edit(edit)
        sublime.set_timeout(lambda: view.set_viewport_position(viewport_position, False), 0)
        view.sel().clear()
        view.show(visible_region, False)
        for sel in selections:
            print('re-adding selection', sel)
            view.sel().add(sel)
        view.set_read_only(READ_ONLY)
        if READ_ONLY:
            view.set_status('Floobits', "You don't have write permission. Buffer is read-only.")

        print('view text is now %s' % get_text(view))
        if save:
            view.run_command("save")

    @staticmethod
    def highlight(buf_id, region_key, username, ranges):
        view = BUF_IDS_TO_VIEWS.get(buf_id)
        if not view:
            print('No view for buffer id', buf_id)
            return
        regions = []
        for r in ranges:
            regions.append(sublime.Region(*r))
        view.erase_regions(region_key)
        view.add_regions(region_key, regions, region_key, 'dot', sublime.DRAW_OUTLINED)

    def id(self, view):
        return view.buffer_id()

    def name(self, view):
        return view.file_name()

    def on_new(self, view):
        print('new', self.name(view))

    def on_load(self, view):
        print('load', self.name(view))

    def on_clone(self, view):
        self.add(view)
        print('clone', self.name(view))

    def on_modified(self, view):
        if not settings.get('run', True):
            return
        try:
            MODIFIED_EVENTS.get_nowait()
        except Queue.Empty:
            self.add(view)
        else:
            MODIFIED_EVENTS.task_done()

    def on_selection_modified(self, view):
        if not settings.get('run', True):
            return
        self.selection_changed.append(view)

    def on_activated(self, view):
        if view.is_scratch():
            return
        self.add(view)
        print('activated', self.name(view))

    def add(self, view):
        vb_id = view.buffer_id()
        # This could probably be more efficient
        for buf_id, v in BUF_IDS_TO_VIEWS.iteritems():
            if v.buffer_id() == vb_id:
                print('view is in BUF_IDS_TO_VIEWS. sending patch')
                self.views_changed.append(view)
                break
        if view.is_scratch():
            print('is scratch')
            return


class FloobitsPromptJoinRoomCommand(sublime_plugin.WindowCommand):

    def run(self, room=""):
        self.window.show_input_panel('Room URL:', room, self.on_input, None, None)

    def on_input(self, room_url):
        parsed_url = urlparse(room_url)
        result = re.match('^/r/([-\w]+)/([-\w]+)/?$', parsed_url.path)
        (owner, room) = result.groups()
        self.window.active_view().run_command('floobits_join_room', {
            'host': parsed_url.hostname,
            'port': parsed_url.port,
            'owner': owner,
            'room': room,
        })


class FloobitsJoinRoomCommand(sublime_plugin.TextCommand):

    def run(self, edit, owner, room, host=None, port=None):

        def run_agent():
            global agent
            try:
                agent = AgentConnection(USERNAME, SECRET, owner, room, host, port)
                agent.connect()
            except Exception as e:
                print(e)
                tb = traceback.format_exc()
                print(tb)

        thread = threading.Thread(target=run_agent)
        thread.start()


class FloobitsPromptMsgCommand(sublime_plugin.WindowCommand):

    def run(self, msg=""):
        print('msg', msg)
        self.window.show_input_panel('msg:', msg, self.on_input, None, None)

    def on_input(self, *args, **kwargs):
        print('msg', args, kwargs)
        self.window.active_view().run_command('floobits_msg', {'msg': msg})


class FloobitsMsgCommand(sublime_plugin.TextCommand):
    def run(self, msg):
        if not msg:
            return
        if agent:
            agent.send_msg(msg)


class MessageCommand(sublime_plugin.TextCommand):
    def run(self, *args, **kwargs):
        pass


Listener.push()
agent = None
