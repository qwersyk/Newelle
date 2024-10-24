pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
moduledir = join_paths(pkgdatadir, 'nyarchassistant')
gnome = import('gnome')

gnome.compile_resources('nyarchassistant',
  'nyarchassistant.gresource.xml',
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
  input: 'nyarchassistant.in',
  output: 'nyarchassistant',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'r-xr--r--'
)

nyarchassistant_sources = [
  '__init__.py',
  'main.py',
  'gtkobj.py',
  'constants.py',
  'settings.py',
  'window.py',
  'shortcuts.py',
  'thread_editing.py',
  'extension.py',
  'llm.py',
  'tts.py',
  'stt.py',
  'extra.py',
  'presentation.py',
  'handler.py',
  'extensions.py'
  'avatar.py',
  'translator.py',
  'dataset.py',
  'smart_prompt.py',
]

install_data(nyarchassistant_sources, install_dir: moduledir)
