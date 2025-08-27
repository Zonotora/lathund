function tocScroll() {
  const main = document.getElementsByTagName("main")[0];
  const anchors = main.querySelectorAll("h1, h2, h3");
  const toc = document.getElementsByClassName("toc");

  if (toc.length == 0) return;

  const nav = toc[0];
  const links = nav.querySelectorAll("a");

  // Make first element active
  if (links.length > 0) {
    links[0].classList.add("active");
  }

  window.addEventListener("scroll", (event) => {
    if (
      typeof anchors != "undefined" &&
      anchors != null &&
      typeof links != "undefined" &&
      links != null
    ) {
      let scrollTop = window.scrollY;

      // highlight the last scrolled-to: set everything inactive first
      links.forEach((link, index) => {
        link.classList.remove("active");
      });

      // then iterate backwards, on the first match highlight it and break
      for (var i = anchors.length - 1; i >= 0; i--) {
        const anchor = anchors[i];
        if (
          anchor != null &&
          scrollTop >= anchor.offsetTop - anchors[0].offsetTop
        ) {
          links[i].classList.add("active");
          break;
        }
      }
    }
  });
}
