# coding: utf-8
import re
import os
import threading
import traceback
from urlparse import urlparse

import sublime_plugin
import sublime

from floo import AgentConnection
from floo.listener import Listener
from floo import shared as G
from floo import utils

settings = sublime.load_settings('Floobits.sublime-settings')

DATA = utils.get_persistent_data()


def update_recent_rooms(room):
    recent_rooms = DATA.get('recent_rooms', [])
    recent_rooms.append(room)
    recent_rooms = recent_rooms[-10:]
    DATA['recent_rooms'] = recent_rooms
    utils.update_persistent_data(DATA)


def reload_settings():
    G.COLAB_DIR = settings.get('share_dir', '~/.floobits/share/')
    G.COLAB_DIR = os.path.expanduser(G.COLAB_DIR)
    G.COLAB_DIR = os.path.realpath(G.COLAB_DIR)
    G.DEFAULT_HOST = settings.get('host', 'floobits.com')
    G.DEFAULT_PORT = settings.get('port', 3148)
    G.USERNAME = settings.get('username')
    G.SECRET = settings.get('secret')

settings.add_on_change('', reload_settings)
reload_settings()


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
                agent = AgentConnection(owner, room, host, port)
                # owner and room name are slugfields so this should be safe
                G.PROJECT_PATH = os.path.realpath(os.path.join(G.COLAB_DIR, owner, room))
                Listener.set_agent(agent)
                agent.connect()
            except Exception as e:
                print(e)
                tb = traceback.format_exc()
                print(tb)
            else:
                joined_room = {'room': room, "owner": owner, "host": host, "port": port}
                update_recent_rooms(joined_room)

        thread = threading.Thread(target=run_agent)
        thread.start()


class FloobitsPromptMsgCommand(sublime_plugin.WindowCommand):

    def run(self, msg=""):
        print('msg', msg)
        self.window.show_input_panel('msg:', msg, self.on_input, None, None)

    def on_input(self, msg):
        self.window.active_view().run_command('floobits_msg', {'msg': msg})


class FloobitsMsgCommand(sublime_plugin.TextCommand):
    def run(self, edit, msg):
        if not msg:
            return

        if agent:
            agent.send_msg(msg)

    def is_visible(self):
        return self.is_enabled()

    def is_enabled(self):
        return agent and agent.is_ready()

    def description(self):
        return "Send a message to the floobits room you are in (join a room first)"

Listener.push()
agent = None
