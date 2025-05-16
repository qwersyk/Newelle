fd -e py >po/POTFILES
xgettext -o po/newelle.pot $(fd -e py)
cd po
for file in $(fd -e po); do
	msgmerge -U "$file" newelle.pot
done
rm -f *~
