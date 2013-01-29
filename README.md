# Floobits plugin for Sublime Text 2

Real-time collaborative editing. Think Etherpad, but with Sublime Text 2.

### Development status: Works, but rough around the edges. It's probably worth waiting a little while for this plugin to mature and stabilize.

## This plugin does not work on Windows. The Python included with the Windows version of Sublime Text 2 does not have the [select](http://docs.python.org/2/library/select.html) module. We're working on fixing this.

# Installation instructions

* If you don't have one already, go to [Floobits](https://floobits.com/) and create an account (or sign in with GitHub).
* Clone this repository or download and extract [this tarball](https://github.com/Floobits/sublime-text-2-plugin/archive/master.zip). **Sublime Text 3 Beta users:** Check out the [ST3 branch](https://github.com/Floobits/sublime-text-2-plugin/tree/st3) or download the [ST3 tarball](https://github.com/Floobits/sublime-text-2-plugin/archive/st3.zip).
* Rename the directory to "Floobits".
* In Sublime Text, go to Preferences -> Browse Packages.
* Drag, copy, or move the Floobits directory into your Packages directory.

If you'd rather create a symlink instead of copy/moving, run something like:

    ln -s ~/code/sublime-text-2-plugin ~/Library/Application\ Support/Sublime\ Text\ 2/Packages/Floobits

# Configuration

Edit your Floobits.sublime-settings file (in `Package Settings -> Floobits -> Settings - User`) and fill in the following info:

    {
      "username": "user",
      "secret": "THIS-IS-YOUR-API-KEY;DO-NOT-USE-YOUR-PASSWORD",
      "share_dir": "~/.floobits/shared/"
    }

Replace username with your Floobits username. The secret is your API secret, which you can see at https://floobits.com/dash/settings/
