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
  'handler.py'
  'avatar.py',
  'translator.py',
  'dataset.py',
  'smart_prompt.py',
]

install_data(newelle_sources, install_dir: moduledir)
