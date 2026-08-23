import app
a = app.app
a.config["TEMPLATES_AUTO_RELOAD"] = True
a.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
a.jinja_env.auto_reload = True
a.run(host="127.0.0.1", port=8090, debug=False, use_reloader=True)
