this folder contains random smol extensions and such, mostly vibe coded- I use them personally and have reviewed the code for issues of course but offer no warranty on their use ;)

## Contents and instructions:

- Fish_tagged.py: a small reverse proxy to send requests to fish audio API with tone tags added by a small intermediate text model. saves mp3 files locally for replay. tone tagging prompt slightly out of date.
  - to set up on Termux:
    1. place in your Termux home, make sure python is installed.
    2. add your OR and Fish keys to the file.
    3. add voices to the file with their unique identifiers.
    4. save, and start the proxy server with `python fish_tagged.py` from the directory in Termux; you should see the server start.
    5. in ST, set up an openrouter compatible endpoint with the following settings:
  <img width="2074" height="1803" alt="Screenshot_20261028-112232" src="https://github.com/user-attachments/assets/bd54a023-df57-4966-8980-ebf180d8fd7f" />
    your voices should be a comma delimited list. if it's set up correctly you should be able to click available voices and get a preview. 
