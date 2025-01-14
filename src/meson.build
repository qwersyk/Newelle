pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
moduledir = join_paths(pkgdatadir, 'newelle')
gnome = import('gnome')

gnome.compile_resources('newelle',
  'newelle.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
)

python = import('python')

conf = configuration_data()
conf.set('PYTHON', python.find_installation('python3').path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', join_paths(get_option('prefix'), get_option('localedir')))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'newelle.in',
  output: 'newelle',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'r-xr--r--'
)

newelle_sources = [
  '__init__.py',
  'main.py',
  'constants.py',
  'window.py',
  'llm.py',
  'tts.py',
  'stt.py',
  'extra.py',
  'handler.py',
  'extensions.py',
]

ui_sources = [
  'ui/__init__.py',
  'ui/profile.py',
  'ui/extension.py',
  'ui/settings.py',
  'ui/presentation.py',
  'ui/thread_editing.py',
  'ui/screenrecorder.py',
  'ui/shortcuts.py',
]

widgets_sources = [
  'ui/widgets/__init__.py',
  'ui/widgets/multiline.py',
  'ui/widgets/profilerow.py',
  'ui/widgets/barchart.py',
  'ui/widgets/comborow.py',
  'ui/widgets/copybox.py',
  'ui/widgets/file.py',
]

install_data(newelle_sources, install_dir: moduledir)
install_data(ui_sources, install_dir: moduledir / 'ui')
install_data(widgets_sources, install_dir: moduledir / 'ui' / 'widgets')
