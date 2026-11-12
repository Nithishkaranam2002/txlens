# R reference for the survival analysis (r_parity/): R 4.4.2 with the survival package it
# ships, plus cmprsk and jsonlite. The rocker image pins CRAN to a dated Posit Package
# Manager snapshot (2025-02-27), so package versions never drift; the digest pins the image.
FROM rocker/r-ver:4.4.2@sha256:df26749182af64d5263bf64149d51a427b476ed28c4e046997143be3f97fdd7c

RUN install2.r --error --skipinstalled cmprsk jsonlite \
    && Rscript -e 'for (p in c("survival", "cmprsk", "jsonlite")) cat(p, format(packageVersion(p)), "\n")'

WORKDIR /app
