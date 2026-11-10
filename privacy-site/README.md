# SoundByte privacy policy site

This is the static site behind https://soundbyte.rjdigitalsolutions.co.uk. DNS has a CNAME to the Netlify site `soundbyte-privacy.netlify.app`.

- One deploy serves both URLs: Play Console currently points at https://soundbyte-privacy.netlify.app (the root), and the custom domain https://soundbyte.rjdigitalsolutions.co.uk is a CNAME to the same Netlify site.
- `/` and `/privacy` both serve the policy, so the Play Console URL keeps working without being changed.
- `_headers` forces `text/html`. The previous deploy was served as `text/plain`, so browsers showed raw code.

Deploy: Netlify → site `soundbyte-privacy` → Deploys → drag this `privacy-site` folder onto the "drag and drop" box. You can also run `netlify deploy --prod --dir privacy-site --site soundbyte-privacy`.
