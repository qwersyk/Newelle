"""Desktop-independent screenshot capture through XDG Desktop Portal."""

import os
import shutil
import time
import uuid
from gettext import gettext as _

from gi.repository import Gio, GLib


def capture_screenshot(directory, interactive=False, is_cancelled=lambda: False):
    """Capture on a worker thread, returning a persistent local PNG path.

    A private main context dispatches portal signals even in headless mode.
    Requires a desktop portal backend implementing the Screenshot interface.
    """
    context = GLib.MainContext.new()
    context.push_thread_default()
    connection = None
    subscription = None
    timer = None
    handle = None
    response = None
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        token = "newelle_" + uuid.uuid4().hex
        sender = connection.get_unique_name()[1:].replace(".", "_")
        handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        loop = GLib.MainLoop.new(context, False)

        def on_response(_connection, _sender, _path, _interface, _signal, parameters, *_user_data):
            nonlocal response
            response = parameters.unpack()
            loop.quit()

        def subscribe(path):
            return connection.signal_subscribe(
                "org.freedesktop.portal.Desktop",
                "org.freedesktop.portal.Request", "Response", path, None,
                Gio.DBusSignalFlags.NONE, on_response,
            )

        # Subscribe before calling Screenshot: a response may arrive immediately.
        subscription = subscribe(handle)
        reply = connection.call_sync(
            "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Screenshot", "Screenshot",
            GLib.Variant("(sa{sv})", ("", {
                "handle_token": GLib.Variant("s", token),
                "interactive": GLib.Variant("b", interactive),
                "modal": GLib.Variant("b", False),
            })),
            GLib.VariantType.new("(o)"), Gio.DBusCallFlags.NONE, 10000, None,
        )
        returned_handle = reply.unpack()[0]
        if returned_handle != handle:
            connection.signal_unsubscribe(subscription)
            handle = returned_handle
            subscription = subscribe(handle)

        deadline = time.monotonic() + 120

        def check_timeout(*_args):
            if is_cancelled() or time.monotonic() >= deadline:
                loop.quit()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        timer = GLib.timeout_source_new(250)
        timer.set_callback(check_timeout)
        timer.attach(context)
        loop.run()
        if is_cancelled():
            raise RuntimeError(_("Screenshot cancelled."))
        if response is None:
            raise RuntimeError(_("Screenshot request timed out."))
        status, results = response
        if status == 1:
            raise RuntimeError(_("Screenshot cancelled."))
        if status != 0:
            raise RuntimeError(_("The desktop portal could not take a screenshot."))
        uri = results.get("uri", "")
        source = Gio.File.new_for_uri(uri).get_path() if uri else None
        if not source or not os.path.isfile(source):
            raise RuntimeError(_("The desktop portal returned no readable screenshot."))
        os.makedirs(directory, mode=0o700, exist_ok=True)
        destination = os.path.join(directory, token + ".png")
        try:
            with open(source, "rb") as image, open(destination, "xb") as saved:
                os.chmod(destination, 0o600)
                shutil.copyfileobj(image, saved)
        except OSError:
            if os.path.exists(destination):
                os.unlink(destination)
            raise
        return destination
    finally:
        if timer is not None:
            timer.destroy()
        if connection is not None:
            if subscription is not None:
                connection.signal_unsubscribe(subscription)
            if handle is not None and response is None:
                try:
                    connection.call_sync(
                        "org.freedesktop.portal.Desktop", handle,
                        "org.freedesktop.portal.Request", "Close", None, None,
                        Gio.DBusCallFlags.NONE, 1000, None,
                    )
                except GLib.Error:
                    pass
        context.pop_thread_default()
