# SoundByte privacy policy site

This is the static site behind https://soundbyte.rjdigitalsolutions.co.uk. DNS has a CNAME to the Netlify site `soundbyte-privacy.netlify.app`.

- `/` and `/privacy` both serve the policy. Use `/privacy` in Play Console.
- `_headers` forces `text/html`. The previous deploy was served as `text/plain`, so browsers showed raw code.

Deploy: Netlify → site `soundbyte-privacy` → Deploys → drag this `privacy-site` folder onto the "drag and drop" box. You can also run `netlify deploy --prod --dir privacy-site --site soundbyte-privacy`.
