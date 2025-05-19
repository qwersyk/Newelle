fd -t f -e py src | xargs rg -l "_\(|N\(_" >po/POTFILES
fd -t f -e in data >>po/POTFILES
xgettext -o po/newelle.pot $(cat po/POTFILES)
cd po
for file in $(fd -e po); do
	msgmerge -U "$file" newelle.pot
done
rm -f *~
