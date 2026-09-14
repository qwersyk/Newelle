import gettext
import os
import uuid

import gi
from gi.repository import Adw, Gio, GLib

_ = gettext.gettext


class ScreenRecorder:
    """Record via the ScreenCast portal on Wayland, or X11 screen capture."""

    BUS_NAME = "org.freedesktop.portal.Desktop"
    OBJECT_PATH = "/org/freedesktop/portal/desktop"
    INTERFACE = "org.freedesktop.portal.ScreenCast"

    def __init__(self, parent_window, on_started=None, on_finished=None):
        self.window = parent_window
        self.on_started = on_started
        self.on_finished = on_finished
        self.recording = False
        self.stopping = False
        self.finished = False
        self.connection = None
        self.session = None
        self.request_path = None
        self.request_subscription = None
        self.session_subscription = None
        self.pipeline = None
        self.bus = None
        self.fd = None
        self.timeout = None
        self.cancellable = Gio.Cancellable()
        self.output_path = os.path.join(
            GLib.get_user_cache_dir(), "screen_records", uuid.uuid4().hex + ".mp4"
        )

    def start(self):
        try:
            # Load only when needed, so missing recording dependencies don't
            # prevent the rest of the application from starting.
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            self.Gst = Gst
            Gst.init(None)
            try:
                gi.require_version("GdkX11", "4.0")
                from gi.repository import GdkX11
                # XWayland cannot capture the full Wayland desktop.
                self.use_x11 = (
                    isinstance(self.window.get_display(), GdkX11.X11Display)
                    and os.environ.get("XDG_SESSION_TYPE") != "wayland"
                    and not os.environ.get("WAYLAND_DISPLAY")
                )
            except (ImportError, ValueError):
                self.use_x11 = False
            self.encoder = next((name for name in ("x264enc", "openh264enc")
                                 if Gst.ElementFactory.find(name)), None)
            self.source_name = "ximagesrc" if self.use_x11 else "pipewiresrc"
            required = (self.source_name, "videoconvert", "videorate", "queue", "h264parse", "mp4mux", "filesink")
            missing = [name for name in required if not Gst.ElementFactory.find(name)]
            if not self.encoder:
                missing.append("x264enc / openh264enc")
            if missing:
                raise RuntimeError(_("Missing GStreamer plugins: %s") % ", ".join(missing))
            os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
            if self.use_x11:
                self._create_pipeline()
                source = self.pipeline.get_by_name("source")
                source.set_property("display-name", self.window.get_display().get_name())
                source.set_property("use-damage", False)
                self._play_pipeline()
                return
            Gio.DBusProxy.new_for_bus(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
                self.BUS_NAME, self.OBJECT_PATH, self.INTERFACE,
                self.cancellable, self._proxy_ready,
            )
        except (ImportError, ValueError, TypeError, RuntimeError, GLib.Error, OSError) as error:
            self._fail(str(error))

    def _proxy_ready(self, source, result):
        try:
            self.proxy = Gio.DBusProxy.new_for_bus_finish(result)
            if self.finished:
                return
            self.connection = self.proxy.get_connection()
            types = self.proxy.get_cached_property("AvailableSourceTypes")
            if types is None or not (types.unpack() & 3):
                raise RuntimeError(_("Screen recording requires PipeWire and a desktop portal backend with ScreenCast support."))
            self.source_types = types.unpack() & 3
            self._request("CreateSession", (), {
                "session_handle_token": GLib.Variant("s", "newelle_" + uuid.uuid4().hex),
            }, self._session_created)
        except (GLib.Error, RuntimeError) as error:
            self._fail(str(error))

    def _request(self, method, args, options, callback):
        token = "newelle_" + uuid.uuid4().hex
        options["handle_token"] = GLib.Variant("s", token)
        sender = self.connection.get_unique_name()[1:].replace(".", "_")
        self.request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        def response(connection, sender, path, interface, signal, parameters):
            self._clear_request()
            if self.finished:
                return
            code, results = parameters.unpack()
            if code == 1:  # The user dismissed the picker.
                self._finish(False)
            elif code != 0:
                self._fail(_("The desktop portal could not start screen recording."))
            else:
                try:
                    callback(results)
                except (GLib.Error, RuntimeError, KeyError, ValueError) as error:
                    self._fail(str(error))

        # Subscribe before calling: a portal may emit Response before the
        # method reply reaches us.
        self.request_subscription = self.connection.signal_subscribe(
            self.BUS_NAME, "org.freedesktop.portal.Request", "Response",
            self.request_path, None, Gio.DBusSignalFlags.NONE, response,
        )
        signatures = {"CreateSession": "(a{sv})", "SelectSources": "(oa{sv})", "Start": "(osa{sv})"}
        self.proxy.call(
            method, GLib.Variant(signatures[method], (*args, options)),
            Gio.DBusCallFlags.NONE, -1, self.cancellable, self._request_sent,
        )

    def _request_sent(self, proxy, result):
        try:
            proxy.call_finish(result)
        except GLib.Error as error:
            self._fail(str(error))

    def _clear_request(self):
        if self.request_subscription is not None:
            self.connection.signal_unsubscribe(self.request_subscription)
            self.request_subscription = None
        self.request_path = None

    def _session_created(self, results):
        self.session = results["session_handle"]
        self.session_subscription = self.connection.signal_subscribe(
            self.BUS_NAME, "org.freedesktop.portal.Session", "Closed",
            self.session, None, Gio.DBusSignalFlags.NONE, self._session_closed,
        )
        options = {
            "types": GLib.Variant("u", self.source_types),
            "multiple": GLib.Variant("b", False),
        }
        cursors = self.proxy.get_cached_property("AvailableCursorModes")
        if cursors is not None and cursors.unpack() & 2:
            options["cursor_mode"] = GLib.Variant("u", 2)
        self._request("SelectSources", (self.session,), options, self._sources_selected)

    def _sources_selected(self, results):
        # An empty parent identifier is valid on both X11 and Wayland.
        self._request("Start", (self.session, ""), {}, self._portal_started)

    def _portal_started(self, results):
        streams = results.get("streams", [])
        if not streams:
            raise RuntimeError(_("No screen was selected for recording."))
        self.stream_id, self.stream_properties = streams[0]
        self.connection.call_with_unix_fd_list(
            self.BUS_NAME, self.OBJECT_PATH, self.INTERFACE, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self.session, {})), GLib.VariantType.new("(h)"),
            Gio.DBusCallFlags.NONE, -1, None, self.cancellable, self._remote_opened,
        )

    def _remote_opened(self, connection, result):
        try:
            reply, descriptors = connection.call_with_unix_fd_list_finish(result)
            if self.finished:
                return
            self.fd = descriptors.get(reply.unpack()[0])
            self._create_pipeline()
            source = self.pipeline.get_by_name("source")
            source.set_property("fd", self.fd)
            serial = self.stream_properties.get("pipewire-serial")
            if serial is not None and source.find_property("target-object"):
                source.set_property("target-object", str(serial))
            else:
                source.set_property("path", str(self.stream_id))
            self._play_pipeline()
        except (GLib.Error, RuntimeError, ValueError, TypeError, OSError) as error:
            self._fail(str(error))

    def _create_pipeline(self):
        encoder = ("x264enc tune=zerolatency speed-preset=ultrafast"
                   if self.encoder == "x264enc" else "openh264enc")
        self.pipeline = self.Gst.parse_launch(
            self.source_name + " name=source do-timestamp=true ! queue ! "
            "videoconvert ! videorate ! video/x-raw,format=I420,framerate=30/1 ! " + encoder +
            " ! h264parse ! mp4mux ! filesink name=output"
        )
        self.pipeline.get_by_name("output").set_property("location", self.output_path)
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect("message", self._pipeline_message)

    def _play_pipeline(self):
        self.timeout = GLib.timeout_add_seconds(30, self._start_timeout)
        if self.pipeline.set_state(self.Gst.State.PLAYING) == self.Gst.StateChangeReturn.FAILURE:
            raise RuntimeError(_("Could not start the screen recording pipeline."))

    def _start_timeout(self):
        self.timeout = None
        self._fail(_("Timed out while starting the screen recording."))
        return GLib.SOURCE_REMOVE

    def _pipeline_message(self, bus, message):
        if self.finished:
            return
        Gst = self.Gst
        if message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            self._fail(error.message)
        elif message.type == Gst.MessageType.EOS:
            self._finish(self.recording)
        elif message.type == Gst.MessageType.STATE_CHANGED and message.src == self.pipeline:
            _old, new, _pending = message.parse_state_changed()
            if new == Gst.State.PLAYING and not self.recording and not self.stopping:
                if self.timeout is not None:
                    GLib.source_remove(self.timeout)
                    self.timeout = None
                self.recording = True
                if self.on_started:
                    self.on_started()

    def _session_closed(self, *args):
        self.session = None
        self.stop()

    def stop(self, *args):
        if self.finished or self.stopping:
            return
        if not self.recording:
            self._finish(False)
            return
        self.stopping = True
        # Keep the portal session and remote alive until the muxer writes EOS.
        self.timeout = GLib.timeout_add_seconds(15, self._stop_timeout)
        if not self.pipeline.send_event(self.Gst.Event.new_eos()):
            self._fail(_("Could not finish the screen recording."))

    def _stop_timeout(self):
        self.timeout = None
        self._fail(_("Timed out while saving the screen recording."))
        return GLib.SOURCE_REMOVE

    def _close_portal_object(self, path, interface):
        self.connection.call(
            self.BUS_NAME, path, interface, "Close", None, None,
            Gio.DBusCallFlags.NONE, -1, None, None,
        )

    def _finish(self, success):
        if self.finished:
            return
        self.finished = True
        self.recording = False
        self.cancellable.cancel()
        if self.timeout is not None:
            GLib.source_remove(self.timeout)
            self.timeout = None
        if self.bus is not None:
            self.bus.remove_signal_watch()
        if self.pipeline is not None:
            self.pipeline.set_state(self.Gst.State.NULL)
            self.pipeline = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.request_path is not None:
            self._close_portal_object(self.request_path, "org.freedesktop.portal.Request")
        self._clear_request()
        if self.session_subscription is not None:
            self.connection.signal_unsubscribe(self.session_subscription)
            self.session_subscription = None
        if self.session is not None:
            self._close_portal_object(self.session, "org.freedesktop.portal.Session")
            self.session = None
        success = success and os.path.isfile(self.output_path) and os.path.getsize(self.output_path) > 0
        if not success:
            try:
                os.unlink(self.output_path)
            except OSError:
                pass
        if self.on_finished:
            self.on_finished(self.output_path if success else None)

    def _fail(self, message):
        if self.finished:
            return
        self._finish(False)
        dialog = Adw.MessageDialog.new(self.window)
        dialog.set_heading(_("Error"))
        dialog.set_body(str(message))
        dialog.set_modal(True)
        dialog.add_response("ok", _("OK"))
        dialog.present()
