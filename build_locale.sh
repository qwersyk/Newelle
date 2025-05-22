(
	find src -type f -name "*.py" -exec grep -lE '_\(|N\(_' {} \;
	find data -type f \( -name "*.xml" -o -name "*.in" \)
) >po/POTFILES
xgettext -o po/newelle.pot $(cat po/POTFILES)
cd po
for file in $(fd -e po); do
	msgmerge -U "$file" newelle.pot
done
rm -f *~
